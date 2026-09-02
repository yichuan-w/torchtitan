#!/usr/bin/env python3
"""Measure pass@k for task packages by solving them on Daytona sandboxes.

The docker-based solve_eval needs a host with docker (flaminio, currently
unreachable); this is the same measurement on the platform training already
uses. Per attempt: boot the package's environment exactly as training rollouts
do (dockerfile+build_context, entrypoint, workspace seeding), let the solver
model drive it one bash command per turn, then grade with the real verifier.
Fresh sandbox per attempt; the reference solution is never staged.

Resumable (task ids already graded in --out are skipped), observable
(per-attempt log lines with timestamps to --log and stdout), reproducible
(each record carries the instruction and full transcript that produced it).

Run on della in the training venv with the Daytona env sourced:
  . ~/.config/daytona/env && SYNTH_ENV_FILE=$ROOT/.synth_env \
    /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python solve_daytona.py \
    --ids ungraded.ids --out results.jsonl [--attempts 5] [--concurrency 16]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack_to_dataset as pack          # noqa: E402
import synth_client as llm              # noqa: E402
import daytona_revalidate as dr         # noqa: E402  (_Root, _start_entrypoint)

from torchtitan.experiments.rl.harness.agents.claude_code import (  # noqa: E402
    boot_agent_sandbox,
)
from torchtitan.experiments.rl.examples.tmax.grading import (  # noqa: E402
    grade_tmax,
    seed_workspace,
)

BASE = Path(os.environ.get("TRL_BASE", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))
# Extra pool roots (colon-separated) searched FIRST -- e.g. evolution/retuned so
# the difficulty of a re-tuned package can be measured, not just the seed's.
_EXTRA = [Path(p) for p in os.environ.get("TRL_EXTRA_POOL", "").split(":") if p]
POOL_ROOTS = _EXTRA + [BASE / "data/tw-extract/tasks", BASE / "data/swe-extract/tasks"]
AGENT_CMD_TIMEOUT = int(os.environ.get("SOLVE_CMD_TIMEOUT", "180"))

log = logging.getLogger("solve_daytona")


def resolve_src(tid: str) -> Path | None:
    for root in POOL_ROOTS:
        d = root / tid
        if (d / "instruction.md").exists():
            return d
    return None


CODEX_URL = os.environ.get(
    "SOLVE_CODEX_URL",
    "https://github.com/openai/codex/releases/latest/download/"
    "codex-x86_64-unknown-linux-musl.tar.gz")


async def _codex_attempt(sb, md: dict, workdir: str, budget: int) -> dict:
    """One attempt driven by the OpenAI Codex CLI inside the sandbox.

    A second measurement instrument beside the bare chat loop -- NOT the one
    the corpus's pass@5/solvable definition was measured with; report its
    numbers as a separate column. Codex runs through the Responses API with a
    coding-optimized context, so it is the instrument to reach for on tasks the
    plain chat filter rejects (HTTP 400 cybersecurity-risk). The API key enters
    the sandbox only as a process env var on an ephemeral sandbox.

    The release tarball unpacks to a SINGLE binary named
    `codex-x86_64-unknown-linux-musl` (not `codex`), so it is installed to a
    fixed path and invoked by that path -- the earlier `--strip-components` +
    bare `codex` invocation is why exit 127 was mistaken for a task failure.
    Install/auth failures return reward=None with a why, never reward=0.
    """
    # Install once per sandbox. Prefer wget when curl is absent (some images
    # ship neither -- those are recorded as install failures, not 0 scores).
    # No heredoc: the harness writes each exec into a script file, and a heredoc
    # body inside that indirection loses its terminator. A single -c avoids it.
    py_prog = (
        "import urllib.request; "
        f"urllib.request.urlretrieve({CODEX_URL!r}, '/tmp/cx.tgz')"
    )
    py_dl = (
        "PYBIN=$(command -v python3 || command -v python); "
        f'"$PYBIN" -c {shlex.quote(py_prog)}'
    )
    install = (
        "test -x /usr/local/bin/codex || { "
        f"( command -v curl >/dev/null 2>&1 && curl -fsSL {CODEX_URL} -o /tmp/cx.tgz ) || "
        f"( command -v wget >/dev/null 2>&1 && wget -qO /tmp/cx.tgz {CODEX_URL} ) || "
        f"( {py_dl} ) || "
        "{ echo NO_DOWNLOADER >&2; exit 90; }; "
        "tar -xzf /tmp/cx.tgz -C /tmp && "
        "mv /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex && "
        "chmod +x /usr/local/bin/codex; }"
    )
    rc, out, err = await sb.exec(install, check=False, timeout=420)
    if rc != 0:
        return {"reward": None, "turns": None,
                "why": f"codex_install_failed(rc={rc}): {(out + err)[-200:]}"}

    key = os.environ.get("OPENAI_API_KEY") or ""
    model = os.environ.get("SOLVE_CODEX_MODEL", "gpt-5.6")
    base = os.environ.get("SYNTH_API_BASE", "https://us.api.openai.com/v1")
    # Prompt via a file to avoid any shell-quoting corruption of the task text.
    await sb.write_file("/tmp/codex_prompt.txt", md["problem_statement"])
    # Three things the codex binary needs to authenticate by API key against
    # our endpoint (verified 2026-08-23): a FRESH CODEX_HOME (a stray ChatGPT
    # login token otherwise wins and 401s on refresh), a model_provider whose
    # base_url is the same us.api endpoint synth_client uses (the default
    # api.openai.com rejects the project key), and the key injected via `env`
    # so the child process actually sees it.
    # CODEX_HOME under /root (not /tmp) so codex will create its PATH-alias
    # helpers -- it refuses to under a temp dir. Feed the prompt on stdin
    # (codex exec reads it) rather than as a positional arg: a `"$(cat)"`
    # positional came through empty and codex then blocked waiting on stdin.
    rc, out, err = await sb.exec(
        "rm -rf /root/.cxhome && mkdir -p /root/.cxhome && "
        f"CODEX_HOME=/root/.cxhome env OPENAI_API_KEY={shlex.quote(key)} "
        "codex exec --dangerously-bypass-approvals-and-sandbox "
        "--skip-git-repo-check "
        "-c model_providers.oai.name=openai "
        f"-c model_providers.oai.base_url={shlex.quote(base)} "
        "-c model_providers.oai.env_key=OPENAI_API_KEY "
        "-c model_provider=oai "
        # No -C: codex inherits the container's own directory, which is
        # where a plain exec and the model's tmux pane both land.
        f"-m {shlex.quote(model)} "
        "- < /tmp/codex_prompt.txt",
        check=False, timeout=budget)
    blob = (out + err)
    # Codex surfaces an API refusal in its own output; distinguish it from a
    # genuine solve attempt so a policy block is not scored as reward 0.
    if rc != 0 and any(s in blob for s in (
            "cybersecurity risk", "flagged", "content_policy",
            "usage policies", "401 Unauthorized", "invalid_api_key")):
        return {"reward": None, "turns": None, "codex_exit": rc,
                "why": f"codex_refused_or_auth: {blob[-200:]}"}
    return {"reward": "pending_grade", "turns": None, "codex_exit": rc,
            "transcript": [{"cmd": "codex exec <instruction>",
                            "out": blob[-4000:]}]}


async def attempt(row: dict, idx: int, max_turns: int,
                  agent: str = "chat") -> dict:
    md = row["metadata"]
    tmax, workdir = md["tmax"], md.get("workdir") or "/workspace"
    instruction = md["problem_statement"]
    history: list[tuple[str, str]] = []
    t0 = time.time()
    try:
        async with boot_agent_sandbox(
            md.get("image") or "",
            dockerfile=md.get("dockerfile") or None,
            build_context=md.get("build_context") or None,
            install_claude=False,
            disk_gb=md.get("daytona_disk_gb"),
        ) as sandbox:
            sb = dr._Root(sandbox)
            if md.get("entrypoint"):
                await dr._start_entrypoint(sb, md["entrypoint"], workdir=workdir)
            await seed_workspace(sb, tmax)
            if agent == "codex":
                a = await _codex_attempt(sb, md, workdir,
                                         budget=max_turns * AGENT_CMD_TIMEOUT)
                if a.get("reward") is None:
                    return {**a, "t": round(time.time() - t0, 1)}
                a["reward"] = await grade_tmax(sb, tmax, workdir=workdir)
                a["t"] = round(time.time() - t0, 1)
                return a
            for turn in range(max_turns):
                try:
                    cmd = await asyncio.to_thread(
                        llm.agent_step, instruction, history)
                except Exception as e:  # noqa: BLE001
                    return {"reward": None, "turns": turn,
                            "why": f"llm: {e}"[:200], "t": time.time() - t0}
                if cmd.strip() == "DONE" or not cmd.strip():
                    break
                rc, out, err = await sb.exec(
                    cmd,
                    check=False, timeout=AGENT_CMD_TIMEOUT)
                history.append((cmd, f"exit={rc}\n{(out + err)[-2000:]}"))
            reward = await grade_tmax(sb, tmax, workdir=workdir)
        return {"reward": reward, "turns": len(history),
                "transcript": [{"cmd": c, "out": o[:800]} for c, o in history],
                "t": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {"reward": None, "turns": len(history),
                "why": f"{type(e).__name__}: {e}"[:250],
                "t": round(time.time() - t0, 1)}


async def solve_task(tid: str, attempts: int, max_turns: int,
                     sem: asyncio.Semaphore, agent: str = "chat") -> dict:
    rec: dict = {"task_id": tid, "t_start": time.time()}
    src = resolve_src(tid)
    if src is None:
        return {**rec, "status": "no_pool_dir"}
    try:
        row = pack.to_row(str(src))
    except Exception as e:  # noqa: BLE001
        return {**rec, "status": "row_error", "why": str(e)[:250]}
    rec["instruction"] = row["metadata"]["problem_statement"]

    async def guarded(i: int) -> dict:
        async with sem:
            a = await attempt(row, i, max_turns, agent=agent)
            log.info("%s attempt %d: reward=%s turns=%s%s t=%.0fs", tid, i,
                     a.get("reward"), a.get("turns"),
                     f" why={a.get('why')}" if a.get("why") else "",
                     a.get("t", 0))
            return a

    rec["attempts"] = list(await asyncio.gather(
        *[guarded(i) for i in range(attempts)]))
    rec["rewards"] = [a.get("reward") for a in rec["attempts"]]
    graded = [a for a in rec["attempts"] if a.get("reward") is not None]
    solved = sum(1 for a in graded if a["reward"] >= 1.0)
    rec.update(graded=len(graded), solved=solved,
               pass_at_k=(solved / len(graded)) if graded else None,
               status=("solved" if solved else
                       "unsolved" if graded else "ungraded"),
               t_end=time.time())
    return rec


async def main_async(args: argparse.Namespace) -> None:
    done: set[str] = set()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        for ln in open(out):
            if ln.strip():
                r = json.loads(ln)
                if r.get("graded"):
                    done.add(r["task_id"])
    ids = [l.strip() for l in open(args.ids) if l.strip()]
    todo = [t for t in ids if t not in done]
    log.info("ids=%d already-graded=%d todo=%d attempts=%d concurrency=%d",
             len(ids), len(done), len(todo), args.attempts, args.concurrency)
    sem = asyncio.Semaphore(args.concurrency)
    with open(out, "a") as f:
        # task-level serial write, attempt-level concurrency via the semaphore;
        # tasks themselves also run concurrently in bounded batches
        for batch_start in range(0, len(todo), args.task_batch):
            batch = todo[batch_start:batch_start + args.task_batch]
            recs = await asyncio.gather(
                *[solve_task(t, args.attempts, args.max_turns, sem, args.agent)
                  for t in batch])
            for rec in recs:
                f.write(json.dumps(rec) + "\n")
            f.flush()
            log.info("checkpoint: %d/%d tasks written",
                     min(batch_start + args.task_batch, len(todo)), len(todo))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--agent", choices=("chat", "codex"), default="chat",
                    help="chat = the bare loop the corpus pass@5 was measured "
                         "with; codex = OpenAI Codex CLI in-sandbox (separate "
                         "instrument, report separately)")
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--concurrency", type=int, default=16,
                    help="max concurrent sandboxes (training holds 768; stay small)")
    ap.add_argument("--task-batch", type=int, default=8,
                    help="tasks in flight per checkpoint batch")
    ap.add_argument("--log", default=str(BASE / "logs/solve_daytona.log"))
    args = ap.parse_args()
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()])
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
