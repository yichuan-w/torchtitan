#!/usr/bin/env python3
"""Agentic retune: change a task with the Codex CLI instead of one chat call.

The chat retune (evolve.simplify) crams the failure traces into a single prompt
truncated to 20k chars, then asks once. This gives Codex the FULL traces as
files in the package and a role via AGENTS.md, and lets it read, focus, and
rewrite agentically -- no truncation, and the role/rules live in a maintainable
file rather than a built string.

Same contract as evolve.simplify / evolve.evolve: takes the task dict, returns
a new task dict with files rewritten and `_hint` set. The output goes through
the SAME revalidation downstream -- this only changes HOW the new files are
written, not the gate they must pass.

Where it works: the rewrite's own `package/` (LAYOUT.md). The loop copies the
input revision there and hardlinks the rollout records under `traces/`; the
agent's cwd is that directory and its edits land in place. Every codex
invocation is one `sessions/<stamp>--<kind>/` beside it: the prompt, both
streams, `session.json`, and a private `codex/` home pruned to the CLI's
session jsonl. Nothing the harness records about a session is in the agent's
view, and nothing the agent writes is outside the package.

Runs Codex LOCALLY on della (the agent reads and rewrites here; the task's own
container it reaches through ./sandbox, on Daytona). Auth mirrors
solve_daytona's verified incantation: a fresh CODEX_HOME (a stray ChatGPT token
otherwise wins and 401s), a model_provider whose base_url is the same us.api
endpoint synth_client uses, and the key injected via env.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import evolve as ev
import synth_client as llm
import task_size as ts
import verifier_literals as vl
from torchtitan.experiments.rl.examples.tmax import layout


class Filtered(RuntimeError):
    """The provider's cybersecurity classifier stopped the session.

    Codex exits 1 with "flagged for possible cybersecurity risk" on stderr and
    writes nothing to stdout. It is not a verdict on the task: the flag fires
    late, once the session's context carries the task's own material (a
    crackme's disassembly, angr's symbolic-execution output), and it is
    probabilistic -- over the three seeds it has ever hit, 11 of 21
    sessions were stopped and 10 ran to the end, four of those producing a
    usable rewrite. So the answer is a fresh session, not a dead task; a
    resumed one would carry the flagged context straight back.
    """


CYBER_FLAG = "flagged for possible cybersecurity risk"
CYBER_RETRIES = int(os.environ.get("CODEX_CYBER_RETRIES", "2"))


class Blocked(Exception):
    """The agent declined the job rather than forcing a pass.

    Distinct from a crash: nothing went wrong, the task simply stays as it is.
    """


CODEX_MODEL = os.environ.get("SYNTH_MODEL", "gpt-5.6")
# Same knob as the chat calls: high unless SYNTH_EFFORT says otherwise. Left
# unset, the CLI ran the sessions at its own default, which the session log
# records as reasoning_effort=null.
CODEX_EFFORT = llm.EFFORT
API_BASE = os.environ.get("SYNTH_API_BASE", "https://us.api.openai.com/v1")
# Turn budget the agent is told to respect; the hard cap is the subprocess
# timeout below. "deadline in the prompt" per the design.
MAX_TOOL_CALLS = int(os.environ.get("CODEX_RETUNE_MAX_CALLS", "25"))
# Which driver runs a session. "exec" is `codex exec`, a batch: whatever the
# agent got wrong is found by the caller afterwards and costs another session.
# "sdk" drives the app-server protocol through codex_session.py, which watches
# the package as it changes and steers the running turn when the rewrite
# leaves a rule -- the correction arrives at minute three instead of in the
# next session. It needs the openai-codex SDK, which lives in its own
# virtualenv (TRL_SDK_PY) rather than the training one.
CODEX_DRIVER = os.environ.get("EVOLVE_CODEX_DRIVER", "exec")
SDK_PY = os.environ.get(
    "TRL_SDK_PY", "/scratch/gpfs/TRIDAO/al9080/terminal-rl/sdkvenv/bin/python")
SESSION_DRIVER = Path(__file__).resolve().parent / "codex_session.py"
TIMEOUT_SEC = int(os.environ.get("CODEX_RETUNE_TIMEOUT", "600"))
# What the harness puts into the package and takes out again before a revision
# is kept: the role file, the container tool, the harness's scratch directory
# and the hardlinked rollout records. Everything else in the package travels.
HARNESS = ("AGENTS.md", "sandbox", "run", "traces")
SCAFFOLD = {"AGENTS.md", "sandbox"}


def _tool_bin() -> Path:
    """``$TRL_BASE/bin``: codex and jq. A path is a convention, not a setting."""
    return layout.Root.from_env().bin


def _codex_bin() -> Path:
    return _tool_bin() / "codex"


def _require_codex() -> None:
    if not _codex_bin().exists():
        raise RuntimeError(f"codex binary not found at {_codex_bin()}")


# --------------------------------------------------------------------------
# One codex invocation, one session directory
# --------------------------------------------------------------------------

@dataclass
class SessionRun:
    """A session while it runs: its directory and the record being built."""

    dir: layout.SessionDir
    meta: dict = field(default_factory=dict)


def _new_session_dir(rewrite: layout.RewriteDir, kind: str) -> layout.SessionDir:
    """The next session name. Stamps are seconds; a second session of one kind
    inside the same second takes the next second's name rather than a random
    suffix, so names stay sortable and say what they are."""
    t = time.time()
    s = rewrite.session(kind, layout.stamp(t))
    while s.path.exists():
        t += 1
        s = rewrite.session(kind, layout.stamp(t))
    return s


def _prune_private_home(home: Path) -> None:
    """Keep the CLI's session jsonl and discard its transient client state.

    Each invocation has a private ``CODEX_HOME``. Files outside ``sessions/``
    are caches the client rebuilds; the jsonl is the transcript.
    """
    if not home.is_dir():
        return
    for child in home.iterdir():
        if child.name == "sessions":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def _link_session_jsonl(src: layout.SessionDir, dst: layout.SessionDir) -> None:
    """Hardlink the recorded session into the resuming session's home.

    ``codex exec resume <id>`` looks the thread up under its own CODEX_HOME
    and appends to the same rollout file, so the resumed session has to find
    it there. A hardlink keeps both directories on one inode: the original
    session's ``codex/`` and the repair's read the same transcript as it
    grows, and each session directory still holds the jsonl of the thread it
    ran.
    """
    sessions = src.codex_home / "sessions"
    for f in sessions.rglob("*.jsonl"):
        layout.link_or_copy(f, dst.codex_home / "sessions" / f.relative_to(sessions))


def cyber_filtered(session: layout.SessionDir) -> bool:
    """Whether the provider's classifier stopped this session.

    Both streams, since codex prints the refusal on stderr today and would
    carry it as an `item.type: "error"` event on stdout under `--json`.
    """
    for stream in (session.stderr, session.stdout):
        try:
            if CYBER_FLAG in stream.read_text(errors="replace"):
                return True
        except OSError:
            pass
    return False


@contextlib.contextmanager
def session(rewrite: layout.RewriteDir, kind: str, *, timeout: int,
            resumes: layout.SessionDir | None = None):
    """One codex invocation: create its directory, record it at start and end.

    ``kind`` is agent, repair, verifier or oracle (LAYOUT.md). ``session.json``
    is written with status ``running`` before anything else, so a loop killed
    mid-session leaves a record that says so and finalize_interrupted_traces
    can mark it. ``resumes`` names the session whose thread this one
    continues; its jsonl is hardlinked in (see ``_link_session_jsonl``).

    The block covers the invocation and its teardown only. What the caller
    decides afterwards -- verdict, checks, whether the rewrite is kept -- is
    the rewrite's record, not the session's.
    """
    sd = _new_session_dir(rewrite, kind)
    sd.path.mkdir(parents=True, mode=0o700)
    # GPFS default ACLs can widen the requested mode. These directories hold
    # the prompt (with the verifier in the package it describes) and the
    # transcript of a session that read the reference solution.
    sd.path.chmod(0o700)
    sd.codex_home.mkdir(mode=0o700)
    meta = {
        "kind": kind, "model": CODEX_MODEL, "reasoning_effort": CODEX_EFFORT,
        "driver": CODEX_DRIVER, "started": layout.stamp(), "finished": None,
        "status": "running", "exit_code": None, "error": None,
        "timeout_sec": timeout, "filtered": False,
    }
    if resumes is not None:
        meta["resumed"] = f"sessions/{resumes.path.name}"
        _link_session_jsonl(resumes, sd)
    layout.write_json_atomic(sd.meta, meta)
    run = SessionRun(sd, meta)
    try:
        yield run
    except BaseException as exc:
        if isinstance(exc, Blocked):
            meta["status"] = "blocked"
        elif isinstance(exc, subprocess.TimeoutExpired):
            meta["status"] = "timed_out"
        else:
            meta["status"] = "failed"
        meta["error"] = f"{type(exc).__name__}: {exc}"[:500]
        raise
    else:
        meta["status"] = "completed"
    finally:
        meta["finished"] = layout.stamp()
        meta["filtered"] = cyber_filtered(sd)
        try:
            _prune_private_home(sd.codex_home)
        except OSError as exc:
            meta["cache_cleanup_error"] = f"{type(exc).__name__}: {exc}"
        layout.write_json_atomic(sd.meta, meta)


# --------------------------------------------------------------------------
# Running the CLI
# --------------------------------------------------------------------------

def _harness_env() -> dict:
    """What ./sandbox needs from the environment, codex or not."""
    env = dict(os.environ)
    # ./sandbox is a copy of agent_sandbox.sh dropped into the package, so from
    # there the script cannot find agent_sandbox.py beside itself. Tell it
    # where the harness lives. Without this every session ends in BLOCKED
    # and the loop records it as `kept`.
    env["EVOLVE_HARNESS_DIR"] = str(Path(__file__).resolve().parent)
    # jq sits beside the codex binary, for reading traces/; the loop's own
    # PATH (a systemd unit's) does not carry that directory.
    env["PATH"] = str(_tool_bin()) + os.pathsep + env.get("PATH", "")
    return env


def _codex_env(sd: layout.SessionDir) -> dict:
    env = _harness_env()
    env["CODEX_HOME"] = str(sd.codex_home)
    env["OPENAI_API_KEY"] = llm._api_key()
    return env


def _provider_overrides() -> list[str]:
    """The provider settings both drivers pass; the SDK takes them as a list."""
    return [
        "model_providers.oai.name=openai",
        f"model_providers.oai.base_url={API_BASE}",
        "model_providers.oai.env_key=OPENAI_API_KEY",
        "model_provider=oai",
    ]


def _session_cmd(run: SessionRun, cwd: Path, *, resume: str | None) -> list[str]:
    cmd = [SDK_PY, str(SESSION_DRIVER), "--pkg", str(cwd),
           "--codex-home", str(run.dir.codex_home),
           "--events", str(run.dir.path / "events.jsonl"),
           "--prompt-file", str(run.dir.prompt),
           "--timeout", str(run.meta["timeout_sec"]),
           "--model", CODEX_MODEL, "--effort", str(CODEX_EFFORT)]
    if resume:
        cmd += ["--resume", resume]
    return cmd


def _codex_cmd(cwd: Path, resume: str | None = None) -> list[str]:
    cmd = [str(_codex_bin()), "exec"]
    if resume:
        cmd += ["resume", resume]
    cmd += [
        "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
        "-c", "model_providers.oai.name=openai",
        "-c", f"model_providers.oai.base_url={API_BASE}",
        "-c", "model_providers.oai.env_key=OPENAI_API_KEY",
        "-c", "model_provider=oai",
        "-c", f"model_reasoning_effort={CODEX_EFFORT}",
    ]
    # `exec resume` takes no -C (codex-cli 0.149: it continues in the
    # session's recorded cwd); the subprocess is started in the package
    # either way.
    if not resume:
        cmd += ["-C", str(cwd)]
    cmd += ["-m", CODEX_MODEL, "-"]
    return cmd


def _read_stream(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _run_codex(run: SessionRun, cwd: Path, prompt: str, *,
               resume: str | None = None) -> subprocess.CompletedProcess:
    """Run codex over ``cwd`` with the session's private CODEX_HOME.

    A stray ChatGPT token in the shared home otherwise wins and 401s, which is
    why the home is per-invocation rather than shared. `resume` continues a
    recorded session (its id) instead of starting one. The prompt goes to
    ``prompt.md`` and to the CLI's stdin; both streams go to disk as the
    process runs.
    """
    sd = run.dir
    timeout = int(run.meta["timeout_sec"])
    sd.prompt.write_text(prompt)
    env = _codex_env(sd)
    if CODEX_DRIVER == "sdk":
        cmd = _session_cmd(run, cwd, resume=resume)
        env["CODEX_BIN"] = str(_codex_bin())
        env["EVOLVE_CODEX_OVERRIDES"] = "\n".join(_provider_overrides())
    else:
        cmd = _codex_cmd(cwd, resume=resume)
    # Streamed to disk rather than captured in memory. A session runs for tens
    # of minutes and used to write nothing until it ended, so a killed one
    # (SIGKILL leaves no record at all) took its log with it, and there was no
    # way to watch a live one. Now `tail -f` works, a kill keeps whatever ran,
    # and the provider's refusal line lands on disk the moment it is printed.
    try:
        with sd.stdout.open("w") as out_f, sd.stderr.open("w") as err_f:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=out_f, stderr=err_f,
                text=True, cwd=str(cwd), env=env,
            )
            try:
                proc.communicate(input=prompt, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
    except subprocess.TimeoutExpired as exc:
        run.meta["exit_code"] = proc.returncode
        # The partial streams are on disk; hand them to the caller too, which
        # is where the old in-memory capture put them.
        raise subprocess.TimeoutExpired(cmd, timeout, output=_read_stream(sd.stdout),
                                        stderr=_read_stream(sd.stderr)) from exc
    run.meta["exit_code"] = proc.returncode
    return subprocess.CompletedProcess(cmd, proc.returncode,
                                       _read_stream(sd.stdout), _read_stream(sd.stderr))


def _session_id(sd: layout.SessionDir) -> str:
    """The id of the thread recorded under this session's CODEX_HOME."""
    newest = None
    for f in (sd.codex_home / "sessions").rglob("*.jsonl"):
        if newest is None or f.stat().st_mtime > newest.stat().st_mtime:
            newest = f
    if newest is None:
        raise RuntimeError(f"no recorded session under {sd.codex_home}")
    with newest.open() as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "session_meta":
                sid = (rec.get("payload") or {}).get("id")
                if sid:
                    return str(sid)
    raise RuntimeError(f"no session_meta in {newest}")


# --------------------------------------------------------------------------
# The package as the agent sees it
# --------------------------------------------------------------------------

def _write_seed_literals(pkg: Path, verifier_rel: str) -> None:
    """What the seed's verifier already depends on unseen, for `./sandbox
    check`'s names audit to subtract: the agent answers for the names its
    rewrite added, not for the seed's. Written once, before the session,
    while the package on disk is still the input revision."""
    path = pkg / "run" / "seed_literals.json"
    if path.exists():
        return
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(vl.audit_package(pkg, verifier_rel)) + "\n")
    # And the seed's size, for the one-rung check the same tool runs.
    (pkg / "run" / "seed_size.json").write_text(
        json.dumps(ts.size_of_package(pkg, verifier_rel)) + "\n")


def _write_resources(pkg: Path, task: dict) -> None:
    """Tell the sandbox tool what size box training gives this task.

    `_resources` is the row's own daytona_cpu/mem_gb/disk_gb filled out with
    the trainer's fleet defaults, resolved by the loop. Without this file the
    tool opens the harness default box (2/4/6), which on this corpus is a size
    up from what training runs at (1/2/2): the agent's check would pass in a
    box the task never gets, and the fold would inherit the seed's size for a
    task that had outgrown it.
    """
    res = task.get("_resources")
    if not res:
        return
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "run" / "resources.json").write_text(json.dumps(res, sort_keys=True) + "\n")


def _write_pretest(pkg: Path, task: dict) -> None:
    """Tell the sandbox tool the row's pin hook, so `./sandbox check` grades
    the way training does: pins first, verifier second.

    `_pretest` is what the loop read off the row into the rewrite's
    pretest.json. This copy under run/ is the agent's, like resources.json:
    it feeds the agent's own check and is stripped at the fold, while the
    loop's probe reads the rewrite's file, which the session cannot reach. A
    task without a hook writes nothing and the tool grades with the verifier
    alone.
    """
    hook = task.get("_pretest")
    if not hook or not hook[0]:
        return
    layout.write_pretest(pkg / "run" / "pretest.json", *hook)


def _prepare_package(pkg: Path, task: dict) -> dict:
    """The package as the loop left it, plus what the agent needs: the seed's
    literals and size for `./sandbox check`, the training box, the role file
    and the container tool. Returns the file map the task was loaded with."""
    # copytree applied the source revision's root mode. Restore the private
    # mode before a prompt or a session sits beside it.
    pkg.chmod(0o700)
    (pkg / "run").mkdir(exist_ok=True)
    fmap = ev.file_map(task)
    _write_seed_literals(pkg, fmap["test_state_py"])
    _write_resources(pkg, task)
    _write_pretest(pkg, task)
    shutil.copy2(SPEC, pkg / "AGENTS.md")
    shutil.copy2(SANDBOX, pkg / "sandbox")
    os.chmod(pkg / "sandbox", 0o755)
    return fmap


def support_changes(pkg: Path, seed_dir: Path | None) -> list[str]:
    """Files other than the four that round-trip which differ from the input
    revision: added, changed, or removed. The harness's own files are not
    the package's.

    The agent validated the package WITH its fixtures, so a Dockerfile whose
    COPY source is a file the agent wrote has to count as a change: a package
    whose only edit was a new fixture would otherwise take the instruction-only
    fast path and never rebuild.
    """
    if not seed_dir or not Path(seed_dir).is_dir():
        return []
    seed_dir = Path(seed_dir)

    def files(root: Path) -> dict[str, Path]:
        out = {}
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(root)
            if rel.parts[0] in HARNESS or "__pycache__" in rel.parts:
                continue
            out[str(rel)] = f
        return out

    now, before = files(pkg), files(seed_dir)
    mapped = set(ev.FILES.values()) | set(ev.VERIFIER_CANDIDATES)
    changed = []
    for rel in sorted(set(now) | set(before)):
        if rel in mapped:
            continue
        if rel not in now or rel not in before or \
                now[rel].read_bytes() != before[rel].read_bytes():
            changed.append(rel)
    return changed


def _last_check(pkg: Path) -> dict | None:
    """The last record `./sandbox check` appended, or None."""
    path = pkg / "run" / "checks.jsonl"
    if not path.exists():
        return None
    last = None
    for line in path.read_text().splitlines():
        if line.strip():
            last = line
    if not last:
        return None
    try:
        return json.loads(last)
    except ValueError:
        return None


def _agent_checked(pkg: Path) -> bool:
    """Whether the agent's last `./sandbox check` passed."""
    return (_last_check(pkg) or {}).get("verdict") == "pass"


def _require_checked(pkg: Path) -> None:
    """AGENTS.md promises that a rewrite which never passed `./sandbox check`
    is discarded whole. Until this, nothing enforced it: the caller's probe
    would find out at its own expense. Measured on wd-20260903b, 298 of 299
    folded sessions had the record anyway, so this bites rarely and keeps the
    promise true."""
    if not _agent_checked(pkg):
        raise RuntimeError("agent finished without a passing ./sandbox check "
                           "(run/checks.jsonl); the rewrite is discarded")


def _check_verdict(pkg: Path) -> None:
    verdict = pkg / "run" / "verdict.txt"
    if verdict.exists():
        text = verdict.read_text().strip()
        head = text.upper()
        # Both are legitimate endings, and both mean the same thing here:
        # leave the task alone. Giving up honestly has to be at least as
        # easy as passing, or the only way to finish is to make the check
        # weaker until it passes.
        if head.startswith("BLOCKED") or head.startswith("GIVE UP"):
            raise Blocked(text[:200])


def _verifier_on_disk(pkg: Path, preferred: str) -> str:
    if (pkg / preferred).exists():
        return preferred
    alt = next((c for c in ev.VERIFIER_CANDIDATES if (pkg / c).exists()), None)
    if alt is None:
        raise RuntimeError(f"no verifier on disk ({ev.VERIFIER_CANDIDATES})")
    return alt


def _collect(task: dict, pkg: Path, fmap: dict) -> dict:
    """Read the package back: the four round-tripping files, which support
    files moved, and what the agent's last passing check measured.

    The agent may rewrite the verifier as the other corpus's form -- grading
    always runs tests/test.sh, and a TW task carrying tests/test_state.py can
    legitimately grow a test.sh and drop the helper -- so read back whichever
    verifier is on disk now, not the path the source happened to carry, and
    carry that path forward so the writeback and the row builder follow the
    file the agent kept. A task that passed ./sandbox check with a switched
    verifier used to crash here on the deleted file and be discarded whole.
    """
    fmap = dict(fmap)
    if not (pkg / fmap["test_state_py"]).exists():
        fmap["test_state_py"] = _verifier_on_disk(pkg, fmap["test_state_py"])
    out = {**task, **{key: (pkg / rel).read_text() for key, rel in fmap.items()}}
    out["_verifier_rel"] = fmap["test_state_py"]
    # What the reference solution cost in the agent's own container, from the
    # last check that passed. The caller sizes the rewritten row from this --
    # a measurement, never the agent's estimate -- and verifies at that size.
    chk = _last_check(pkg) or {}
    if chk.get("verdict") == "pass":
        out["_measured"] = chk.get("measured")
        out["_box"] = chk.get("resources")
        out["_at_max"] = bool(chk.get("at_max"))
    out["_support_changed"] = support_changes(pkg, task.get("_seed_dir"))
    return out


def _sandbox_down(pkg: Path) -> None:
    """Delete the container a session left running. Best effort."""
    state = pkg / "run" / "sandbox.json"
    if not state.exists():
        return
    try:
        if json.loads(state.read_text()).get("status") == "down":
            return
    except ValueError:
        pass
    try:
        subprocess.run([str(pkg / "sandbox"), "down"], cwd=pkg, env=_harness_env(),
                       capture_output=True, text=True, timeout=300)
    except Exception:  # noqa: BLE001 -- cleanup must not turn a verdict into a crash
        pass


def _harness_check(pkg: Path, name: str = "check") -> str:
    """Run `./sandbox check` in the author's package from the harness rather
    than from a session, and return what it printed. Used when the verifier
    was written elsewhere: the record it appends to run/checks.jsonl is the
    same one _require_checked reads, and the text is kept as run/<name>.txt
    for whoever reads a rejected rewrite."""
    try:
        p = subprocess.run([str(pkg / "sandbox"), "check"], cwd=pkg, env=_harness_env(),
                           capture_output=True, text=True, timeout=AGENT_TIMEOUT)
        text = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    except subprocess.TimeoutExpired as exc:
        text = f"./sandbox check timed out after {AGENT_TIMEOUT}s\n{exc.stdout or ''}"
    finally:
        _sandbox_down(pkg)
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "run" / f"{name}.txt").write_text(text)
    return text


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_AGENTS_MD = """# Your job: make ONE terminal task easier, by rewriting its instruction only

You are re-tuning a training task an agent kept failing. Files in this directory:

- `instruction.md` — the task as given to the agent. THIS is the only file you edit.
- `environment/Dockerfile` — how the task's container is built (context, read-only).
- `{verifier}` — the private verifier that grades a solution (context, read-only).
- `solution/solve.sh` — a reference solution, if present (context, read-only).
- `traces/attempt-NN.jsonl` — the FULL failed attempts, one file each: every turn's
  commands and the terminal output that followed (format under TRACES in the prompt).

The agent solved this {solved} of {attempts} attempts — too hard. Rewrite
`instruction.md` so a capable agent lands it about half the time.

## How to make it easier ({level})
{level_rule}

## Hard rule — never leak the verifier
Never name the verifier in the instruction: no test file paths (tests/...), no
test or function names, no `pytest` / `test.sh` command to run. Point at the
BEHAVIOUR to fix or where in the source to look — naming the check that grades it
hands over the answer, and the task is then rejected. The verifier is given to you
only so you know what NOT to reveal; never weaken or reference it.

## Work efficiently
Read the traces to find where the agent actually got stuck, then make the smallest
edit that clears that one failure. At most {max_calls} tool calls. When done, the
rewritten `instruction.md` is your entire output — do not print it, just save it.
"""

_PROMPT = ("Read AGENTS.md and the files it points to, then rewrite instruction.md "
           "in place to make this task easier as instructed. Save it and stop.")


_ORACLE_AGENTS_MD = """# Your job: make the reference solution pass the verifier

This task was just rewritten to be harder. The rewrite regenerated the
instruction, the solution and the verifier together, and they do not agree: the
reference solution was run against the verifier and FAILED. Fix that.

Files in this directory:

- `solution/solve.sh` — the reference solution. Usually the file to fix.
- `{verifier}` — the verifier that grades it. Fix only where it is unfair.
- `instruction.md` — what the agent is told (context; edit only if it promises
  something the verifier does not check, or omits something the verifier needs).
- `environment/Dockerfile` — how the container is built (context, read-only).
- `run/failure.txt` — what actually happened when the solution ran, exit code
  {exit_code}. Start here. You are not guessing about behaviour: this is the
  behaviour.

## Method
Read `run/failure.txt` first and let it decide where you look — an impression of
what the code should do is what produced this failure. Identify the specific
check that failed, find the lines of `solve.sh` responsible for it, and fix
those. Do not rewrite what the run shows is already working.

## Which side to change
Prefer `solve.sh`. The verifier encodes what the task asks for, and weakening it
to fit an incomplete solution is how a task becomes worthless. Change a check
only when it demands something the instruction never promised and the workspace
never reveals — such a check would fail a capable agent too. If a check re-runs
the workflow after changing an input, the solution has to recompute from the
inputs as they are at that moment; a hard-coded answer passes the first run and
fails the re-run.

If the failure is environmental rather than logical — a missing tool, no network
— do not code around it. Write `BLOCKED: <reason>` to `run/verdict.txt` and stop.

## Work efficiently
At most {max_calls} tool calls. Save your edits in place; do not print the files.
"""

_ORACLE_PROMPT = ("Read AGENTS.md, then read run/failure.txt and fix the task so "
                  "the reference solution passes the verifier. Save your edits "
                  "in place and stop.")


_TRACES_SPEC = r"""

TRACES. `traces/attempt-NN.jsonl`, one file per attempt (NN counts from 01,
in the order the attempts ran), one JSON object per line. Line 1 is the
rollout: reward, turns, secs, and how it ended (status, finish_reason,
submitted, format_errors, infra_failed), with the task, run and group it
came from. Every later line is one turn, in order: turn, keystrokes (a list:
what the agent typed; empty on a closing turn), task_complete, output (the
terminal after it; empty on the last turn, where the episode ended),
analysis, plan, think. A turn with no parseable response has raw instead of
keystrokes. jq is on PATH; these are enough:

  head -qn1 traces/*.jsonl | jq -c '{rollout, reward, turns, finish_reason}'
                                                              # every attempt's outcome
  jq -r 'select(.turn) | "\(.turn)\t" + ((.keystrokes // [.raw]) | join(""))' traces/attempt-01.jsonl
                                                              # the commands alone, turn number first
  tail -n 4 traces/attempt-03.jsonl | jq -r '.turn, .output'  # what the last four turns saw"""


def _traces_spec(traces: Path) -> str:
    """The format paragraph, only when the package carries attempt records."""
    return _TRACES_SPEC if any(traces.glob("attempt-*.jsonl")) else ""


# --------------------------------------------------------------------------
# Instruction-only easier rewrite, and the fresh-session oracle repair
# --------------------------------------------------------------------------

def simplify_codex(rewrite: layout.RewriteDir, task: dict, solved: int = 0,
                   attempts: int = 16, hint: str = "vague") -> dict:
    """Codex-driven counterpart of evolve.simplify: the instruction rewritten in
    place, with the failed attempts on disk under ``traces/``. Returns a new
    task dict with `instruction` rewritten and `_hint="codex"`. Raises on
    hard failure."""
    _require_codex()
    pkg = rewrite.package
    fmap = ev.file_map(task)
    level = "specific" if any(rewrite.traces.glob("attempt-*.jsonl")) else "vague"
    if hint == "none":
        level = "none"
    (pkg / "AGENTS.md").write_text(_AGENTS_MD.format(
        verifier=ev._verifier_rel(task), solved=solved, attempts=attempts,
        level=level, level_rule=ev.HINT_LEVELS[level], max_calls=MAX_TOOL_CALLS))
    with session(rewrite, "agent", timeout=TIMEOUT_SEC) as run:
        p = _run_codex(run, pkg, _PROMPT + _traces_spec(rewrite.traces))
    new_instruction = (pkg / fmap["instruction"]).read_text()
    if not new_instruction.strip():
        raise RuntimeError("codex emptied the instruction")
    if new_instruction == task["instruction"]:
        raise RuntimeError(f"codex left the instruction unchanged "
                           f"(exit {p.returncode}): {p.stdout[-200:]}")
    return {**task, "instruction": new_instruction, "_hint": "codex",
            "_session": str(run.dir.path)}


def repair_oracle_codex(rewrite: layout.RewriteDir, task: dict, observed: str,
                        exit_code: int = 1) -> dict:
    """Fresh-session repair of a task whose reference solution failed the run.

    The chat repair reads the two files and guesses which side is wrong. Here the
    agent has both files on disk at full length and the run output that proves
    they disagree, and can grep between them instead of holding a 500-line
    solution and a 500-line verifier in one prompt. `resume_agentic` is the
    better tool when the session that wrote the files is on disk; this is for
    when it is not. Raises on failure.
    """
    _require_codex()
    pkg = rewrite.package
    fmap = ev.file_map(task)
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "run" / "failure.txt").write_text(observed or "(no output captured)")
    (pkg / "run" / "verdict.txt").unlink(missing_ok=True)
    (pkg / "AGENTS.md").write_text(_ORACLE_AGENTS_MD.format(
        verifier=ev._verifier_rel(task), exit_code=exit_code,
        max_calls=MAX_TOOL_CALLS))
    with session(rewrite, "oracle", timeout=TIMEOUT_SEC) as run:
        p = _run_codex(run, pkg, _ORACLE_PROMPT)
    _check_verdict(pkg)
    out = {**task, **{key: (pkg / rel).read_text() for key, rel in fmap.items()}}
    if all(out[key] == task[key] for key in fmap):
        raise RuntimeError(f"codex changed nothing (exit {p.returncode}): "
                           f"{p.stdout[-200:]}")
    out["_repaired"] = "codex_oracle_observed"
    out["_session"] = str(run.dir.path)
    return out


# --------------------------------------------------------------------------
# Agentic evolution: one session, with the task's own container as a tool
# --------------------------------------------------------------------------

SPEC = Path(__file__).resolve().parent / "agents" / "task_evolution.md"
SANDBOX = Path(__file__).resolve().parent / "agent_sandbox.sh"
# Who writes the verifier of a harder rewrite. "same": the session that wrote
# the solution, as it always has. "blind": a second session that is shown the
# instruction, the environment and the seed's verifier, and not the solution,
# so it cannot inherit the solution's private vocabulary -- the hidden
# contract that failed five of eight reviewed 0/16 tasks is then impossible
# by construction rather than caught by a heuristic afterwards. It costs a
# second session and one more ./sandbox check per rewrite: on the first paired
# round (wd-evolve-dev/ab/20260904-170942, six 16/16 signals each way) the
# per-task time went from a median of 430 s to 974 s and the round's wall
# clock from 1323 s to 2320 s on three workers, with the same six of six
# folded, the same sizes, and the hidden solution passing the blind verifier
# at first meeting on all six. The verifiers it wrote recompute the expected
# result from the container instead of asserting the solution's strings,
# which is what the split was for. "same" remains for comparison.
VERIFIER_AUTHOR = os.environ.get("SWE_VERIFIER_AUTHOR", "blind")
VERIFIER_SPEC = Path(__file__).resolve().parent / "agents" / "verifier_author.md"
# What the verifier's author must not see. Everything else in the package is
# what an agent attempting the task could read.
HIDDEN_FROM_VERIFIER = ("solution", "traces", "AGENTS.md", "sandbox",
                        "run/checks.jsonl", "run/verdict.txt", "run/failure.txt",
                        "run/sandbox.json", "run/sandbox.log")
AGENT_TIMEOUT = int(os.environ.get("EVOLVE_AGENT_TIMEOUT", "2400"))

_HARDER_JOB = """This task was solved {solved} of {attempts} attempts, so it is too
easy to teach anything. Make it one rung harder, along exactly one of these
axes:

{candidates}

One rung, not a new task. Keep everything the seed asks for and add ONE
requirement that the agent which solved it never had to meet. The attempts
that solved it are in `traces/`, one file per attempt (format under TRACES at
the end). What made the task easy is visible there: the guidance the
instruction handed over, the step the agent never had to work out. Before you
choose the axis, list the commands of two or three attempts end to end; the
attempt that solved it in the fewest turns says which step was free.

The size of the rewrite is checked, not trusted. The reference solution may
grow by {min_added} to {max_added} non-comment lines over the seed's
{seed_lines}; the verifier may gain at most {max_asserts} assertions over the
seed's {seed_asserts}. `./sandbox check` fails outside that and the caller
rejects the rewrite. Measured on this corpus: rewrites that grew to 125 lines
came back 0/16 five times in six, while the seed at its own size was solved
every time. A harder task is one more thing to get right, not a new workflow.

Pick from that list and nothing else. The list is not a menu of equals — it is
ordered, and the order was computed against the whole task pool: which
transformations are under-represented right now, and which ones this task has a
foothold for. Work down it and take the first axis this package genuinely
supports, so that substituting whichever is easiest to write cannot quietly
collapse the pool onto a few kinds of change. Then write that axis's id, alone
on one line, to `run/operator.txt`, before you start changing anything.

Then do the work in this order, one file at a time. The order is not
arbitrary — each file is written against the one before it, and the synthesis
pipeline that produced these tasks runs the same sequence for that reason:

  1. `solution/solve.sh` — what the answer now is under this axis: the seed's
     solution plus the one new step, not a rewrite of it.
  2. the verifier — written against that answer and against the axis, not
     against incidental details of the workspace.
  3. `instruction.md` — what the agent is told. It has to make everything the
     verifier requires discoverable: findable in the workspace, or stated here.
  4. `environment/Dockerfile` — the environment the other three assume.

Add whatever new files the change needs — a fixture the Dockerfile COPYs, a
config, a data file. Anything you write in the package comes back with it.

Constraints on the environment, because step 4 is yours now and these are what
the pipeline that built these tasks learned the hard way:

- **Every COPY source must exist in the package.** A Dockerfile line referring to
  a file you did not write is the most common way a rewritten task is thrown
  away: it builds nowhere, and the failure arrives long after this session ended.
- **What you add has to fit in the row.** The package is shipped to training as
  one line of JSON: COPY sources together stay under 1 MiB, and files under
  `tests/` are text and together stay under 1 MiB; a binary under `tests/` is
  refused by name (`./sandbox up` tells you). A binary the environment needs is
  a COPY source under `environment/` or is produced by a RUN; a large reference
  the verifier needs is checked by hash, not shipped.
- Preserve the seed's base image and installation style; make the smallest change
  the axis needs. An environment rewritten wholesale is a new task, not a harder
  one.
- No internet-only runtime behaviour, no proxies, credentials or external
  services. The sandbox may have none of them and the reference solution will
  fail where an agent would too.
- The container is the size training gives this task (`run/resources.json`),
  and `./sandbox check` measures what the reference solution costs in it. The
  task is provisioned from that measurement, never below the seed's size and
  never from a number you write. If the solution outgrows the box, `check`
  says what ran out; make the solution need less. `./sandbox check --max`
  measures at the platform ceiling (4 vCPU / 8 GiB / 10 GiB) and is for the
  rare harder task that genuinely needs more than the seed had, not a way
  past a failing check. A task that needs more than the ceiling never starts,
  so it is unrunnable rather than hard.
- Guard edits against paths you did not create (`test -f` first); prefer adding a
  local fixture over patching something the image cloned.

**Before you run `./sandbox check`, reconcile the verifier against the solution
one check at a time.** For each check, name the lines of `solve.sh` that make it
pass. A check with nothing behind it is the single largest way these tasks are
lost: the reference runs cleanly, scores zero, and the whole task is thrown away
after a full image build. Where nothing satisfies a check, fix `solve.sh` if the
check is fair, and fix the check if it asks for something the instruction never
promised and the workspace never reveals — that one would fail a real agent too.
Do this literally, check by check; an impression that it all hangs together is
what produces the failure.

If, having read the package, you judge that none of the listed axes fits this
task, write `GIVE UP: operator-misfit — <why>` and stop. Say which ones you
considered and what was missing for each; a later round will come back with
different counts, and that note is what it reads.

Aim for a task a capable agent lands about half the time."""

_HARDER_JOB_BLIND = """This task was solved {solved} of {attempts} attempts, so it is too
easy to teach anything. Make it one rung harder, along exactly one of these
axes:

{candidates}

One rung, not a new task. Keep everything the seed asks for and add ONE
requirement that the agent which solved it never had to meet. The attempts
that solved it are in `traces/`, one file per attempt (format under TRACES at
the end). What made the task easy is visible there: the guidance the
instruction handed over, the step the agent never had to work out. Before you
choose the axis, list the commands of two or three attempts end to end; the
attempt that solved it in the fewest turns says which step was free.

The size of the rewrite is checked, not trusted. The reference solution may
grow by {min_added} to {max_added} non-comment lines over the seed's
{seed_lines}; the verifier may gain at most {max_asserts} assertions over the
seed's {seed_asserts}. `./sandbox check` fails outside that and the caller
rejects the rewrite. Measured on this corpus: rewrites that grew to 125 lines
came back 0/16 five times in six, while the seed at its own size was solved
every time. A harder task is one more thing to get right, not a new workflow.

Pick from that list and nothing else. The list is not a menu of equals — it is
ordered, and the order was computed against the whole task pool: which
transformations are under-represented right now, and which ones this task has a
foothold for. Work down it and take the first axis this package genuinely
supports, so that substituting whichever is easiest to write cannot quietly
collapse the pool onto a few kinds of change. Then write that axis's id, alone
on one line, to `run/operator.txt`, before you start changing anything.

Then do the work in this order, one file at a time. The order is not
arbitrary — each file is written against the one before it, and the synthesis
pipeline that produced these tasks runs the same sequence for that reason:

  1. `solution/solve.sh` — what the answer now is under this axis: the seed's
     solution plus the one new step, not a rewrite of it.
  2. `instruction.md` — what the agent is told. Everything the new requirement
     needs checked has to be discoverable from it and from the files the image
     ships, because that is all the verifier's author will see (below).
  3. `environment/Dockerfile` — the environment the other two assume.

**Leave `tests/` exactly as it is.** The verifier for this rung is written by a
second session that is shown the instruction, the environment and the seed's
verifier, and not your solution -- so it cannot depend on a name only your
solution knows. `./sandbox check` here grades your solution with the seed's
verifier: it must still pass (the seed's checks are the floor) and the untouched
workspace must still fail. It cannot see the new requirement; the other session
will, from the instruction alone. A name your solution invents and the
instruction never states will not be checked, so state it, or make the result
checkable by value.

Add whatever new files the change needs — a fixture the Dockerfile COPYs, a
config, a data file. Anything you write in the package comes back with it.

Constraints on the environment, because step 4 is yours now and these are what
the pipeline that built these tasks learned the hard way:

- **Every COPY source must exist in the package.** A Dockerfile line referring to
  a file you did not write is the most common way a rewritten task is thrown
  away: it builds nowhere, and the failure arrives long after this session ended.
- **What you add has to fit in the row.** The package is shipped to training as
  one line of JSON: COPY sources together stay under 1 MiB, and files under
  `tests/` are text and together stay under 1 MiB; a binary under `tests/` is
  refused by name (`./sandbox up` tells you). A binary the environment needs is
  a COPY source under `environment/` or is produced by a RUN; a large reference
  the verifier needs is checked by hash, not shipped.
- Preserve the seed's base image and installation style; make the smallest change
  the axis needs. An environment rewritten wholesale is a new task, not a harder
  one.
- No internet-only runtime behaviour, no proxies, credentials or external
  services. The sandbox may have none of them and the reference solution will
  fail where an agent would too.
- The container is the size training gives this task (`run/resources.json`),
  and `./sandbox check` measures what the reference solution costs in it. The
  task is provisioned from that measurement, never below the seed's size and
  never from a number you write. If the solution outgrows the box, `check`
  says what ran out; make the solution need less. `./sandbox check --max`
  measures at the platform ceiling (4 vCPU / 8 GiB / 10 GiB) and is for the
  rare harder task that genuinely needs more than the seed had, not a way
  past a failing check. A task that needs more than the ceiling never starts,
  so it is unrunnable rather than hard.
- Guard edits against paths you did not create (`test -f` first); prefer adding a
  local fixture over patching something the image cloned.

If, having read the package, you judge that none of the listed axes fits this
task, write `GIVE UP: operator-misfit — <why>` and stop. Say which ones you
considered and what was missing for each; a later round will come back with
different counts, and that note is what it reads.

Aim for a task a capable agent lands about half the time."""

_EASIER_JOB = """This task was solved {solved} of {attempts} attempts — the agent
never got there, so it teaches nothing either.

The failed attempts are in `traces/`, one file per attempt (format under
TRACES at the end). Read the last turns of several of them to find where the
agent actually got stuck: the same failing command, the same missing file, the
same misreading of the instruction. Then make the smallest change that clears
that one obstacle. Prefer adding to the instruction
what a fair task would have said; only take structure out of the task itself if
the instruction cannot carry it.

Aim for a task a capable agent lands about half the time."""

_REPAIR_JOB = """Your rewrite did not survive the caller's check. It rebuilt the
package from scratch, ran `solution/solve.sh` against the verifier (exit
{exit_code}) and audited what the verifier demands against what an agent can
see. The verdict and the run's output are in `run/failure.txt`.

Read that first. If the run failed, find the check that failed and the lines
of `solution/solve.sh` responsible for it, and fix them; prefer fixing the
solution, and change the verifier only where it demands something the
instruction never promised and the workspace never reveals. If the verdict
names paths the verifier requires that nothing the agent can see reveals, name
them where the agent will read them (the instruction, or a file in the image
the instruction points at), or make the verifier stop depending on them; do
not weaken what it checks otherwise. If the verdict says the rewrite is more
than one rung above the seed, take requirements out until it is one: the
seed's deliverable plus one new thing. Do not rewrite what the run shows is
already working. The container you had is gone; `./sandbox up` gives you a
fresh one.

Confirm with `./sandbox check` before you stop."""

_VERIFIER_JOB = """The task in this package was just made one rung harder: `instruction.md` now
asks for one more thing than the seed did. Write the verifier for the task as the
instruction states it.

You are shown the instruction, `environment/`, and the seed's verifier at
`{verifier_rel}` (what the task checked before this rung; it has {seed_asserts}
assertions). You are not shown the reference solution, on purpose; AGENTS.md says
why. Keep every existing check that still holds and add at most {max_asserts}
assertions for the new requirement, each satisfiable by an agent that has read
only the instruction and explored the container. Where the instruction leaves a
name open, check the value.

Then do the task yourself through `./sandbox exec`, the way the instruction
describes it, and `./sandbox grade`: it must pass. `./sandbox reset` and grade
the untouched workspace: it must fail. Both, before you finish."""

_VERIFIER_REPAIR_JOB = """The verifier you wrote does not agree with the task's reference solution, which
you cannot see: the caller ran that solution in a fresh container (exit
{exit_code}) and graded it with your verifier, and it failed. The run's output
is in `run/failure.txt`.

Read it first. The instruction is the contract. The usual cause is a check that
demands something the instruction does not ask for, or a name the instruction
leaves open: loosen that check to what the instruction actually promises, or
check the value instead of the name. Do not drop a check the seed's verifier
already had, and do not weaken a check the instruction plainly requires. If the
run failed a check the instruction does require, the solution is what is wrong:
write `BLOCKED: <which check, and what the run showed>` to `run/verdict.txt` and
stop, and the caller sends the solution back.

If instead the verdict says the verifier is more than one rung above the seed --
too many assertions over the seed's count -- the solution passed and the size
is what failed: fold the checks for the new requirement into fewer assertions,
or drop the ones that restate what the seed's already cover, until the count is
within the limit. Measured on the first paired round, that was the one repair
of six blind rewrites: ten assertions against a seed's four, trimmed to pass."""


_BUDGET = """

Budget: this session is ended at {deadline} ({budget_min} minutes from now),
whatever state the files are in, and a session that ends that way is discarded
whole. `./sandbox up`, `reset` and `check` build the image and take minutes each;
`./sandbox exec` takes seconds. Plan for two or three checks, not for guessing."""


def _budget(timeout: int) -> str:
    deadline = time.strftime("%H:%M %Z", time.localtime(time.time() + timeout))
    return _BUDGET.format(deadline=deadline, budget_min=timeout // 60)


def _candidates(operator: list[tuple[str, str, str]] | None) -> str:
    """The scored shortlist, each entry with its full card.

    The one-line definition is what the pool's scan matches on; the card is
    what the authors wrote for whoever builds the task: what the seed needs to
    have, how the harder version is constructed, what the verifier checks and
    how a shortcut is refused. The agent used to get the line only.
    """
    cands = list(operator or [])
    if not cands:
        return "    (none)"
    parts = []
    for i, (fam, op, defn) in enumerate(cands, 1):
        card = llm.operator_card(op)
        card_lines = "\n".join("       " + line for line in card.splitlines())
        parts.append(f"    {i}. {op} ({fam})\n       {defn}\n{card_lines}")
    return "\n".join(parts)


def _blind_layout(pkg: Path, vpkg: Path) -> None:
    """The author's package minus what the verifier's author must not see."""
    hidden = set(HIDDEN_FROM_VERIFIER)

    def ignore(d, names):
        rel = Path(d).relative_to(pkg)
        return {n for n in names if str(rel / n) in hidden or (str(rel) == "." and n in hidden)}

    shutil.copytree(pkg, vpkg, dirs_exist_ok=True, ignore=ignore)
    vpkg.chmod(0o700)
    (vpkg / "run").mkdir(exist_ok=True)
    shutil.copy2(VERIFIER_SPEC, vpkg / "AGENTS.md")
    shutil.copy2(SANDBOX, vpkg / "sandbox")
    os.chmod(vpkg / "sandbox", 0o755)


def _take_verifier(vpkg: Path, pkg: Path, seed_rel: str, seed_text: str) -> str:
    """Copy the verifier the blind author wrote into the author's package,
    replacing the seed's, and return its path. Raises when nothing changed."""
    rel = _verifier_on_disk(vpkg, seed_rel)
    text = (vpkg / rel).read_text()
    if rel == seed_rel and text == seed_text:
        raise RuntimeError("verifier author changed nothing")
    if rel != seed_rel:
        (pkg / seed_rel).unlink(missing_ok=True)
    dest = pkg / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    # The author's last check graded the seed's verifier; it says nothing
    # about this one, so it must not satisfy _require_checked.
    (pkg / "run" / "checks.jsonl").unlink(missing_ok=True)
    return rel


def _blind_verifier(rewrite: layout.RewriteDir, task: dict,
                    fmap: dict) -> tuple[layout.SessionDir, str]:
    """Second session: write the verifier without seeing the solution.

    Lays the author's package out again under the verifier session's own
    directory with `solution/` and the harness's own files removed, runs one
    session against AGENTS.md = verifier_author.md, and copies the verifier it
    wrote back into the author's package. Returns that session and the
    verifier's path. The caller then runs the check that the two sessions
    never could: the hidden solution against the blind verifier.
    """
    pkg = rewrite.package
    seed_rel = fmap["test_state_py"]
    seed_text = task["test_state_py"]
    seed_size = ts.size_of(task["solve_sh"], seed_text,
                           "python" if seed_rel.endswith(".py") else "shell")
    with session(rewrite, "verifier", timeout=AGENT_TIMEOUT) as run:
        vpkg = run.dir.package
        _blind_layout(pkg, vpkg)
        prompt = _VERIFIER_JOB.format(verifier_rel=seed_rel,
                                      seed_asserts=seed_size["verifier_asserts"],
                                      max_asserts=ts.MAX_ADDED_ASSERTS) + _budget(AGENT_TIMEOUT)
        try:
            _run_codex(run, vpkg, prompt)
        finally:
            _sandbox_down(vpkg)
    _check_verdict(vpkg)
    rel = _take_verifier(vpkg, pkg, seed_rel, seed_text)
    return run.dir, rel


def _blind_repair(rewrite: layout.RewriteDir, vsession: layout.SessionDir,
                  fmap: dict, observed: str, exit_code: int) -> str:
    """Resume the verifier's session with the failure the hidden solution
    produced against its verifier, and take the repaired verifier back."""
    pkg, vpkg = rewrite.package, vsession.package
    seed_rel = fmap["test_state_py"]
    sid = _session_id(vsession)
    (vpkg / "run").mkdir(exist_ok=True)
    (vpkg / "run" / "failure.txt").write_text(observed or "(no output captured)")
    (vpkg / "run" / "verdict.txt").unlink(missing_ok=True)
    with session(rewrite, "repair", timeout=AGENT_TIMEOUT, resumes=vsession) as run:
        prompt = _VERIFIER_REPAIR_JOB.format(exit_code=exit_code) + _budget(AGENT_TIMEOUT)
        try:
            _run_codex(run, vpkg, prompt, resume=sid)
        finally:
            _sandbox_down(vpkg)
    _check_verdict(vpkg)
    before = (pkg / _verifier_on_disk(pkg, seed_rel)).read_text()
    return _take_verifier(vpkg, pkg, _verifier_on_disk(pkg, seed_rel), before)


def _reconcile_blind(rewrite: layout.RewriteDir, vsession: layout.SessionDir,
                     fmap: dict) -> None:
    """Hidden solution against blind verifier, with one bounded repair.

    The two sessions never saw each other's file, so the first time they meet
    is here. A failure means the verifier asks for something the instruction
    did not promise, or the solution does not do what the instruction says;
    the verifier's author gets the run once and decides which, and a second
    failure discards the rewrite. The seed goes back into training unchanged,
    which is the safe outcome for a pair that cannot agree.
    """
    pkg = rewrite.package
    text = _harness_check(pkg, name="check.blind1")
    if _agent_checked(pkg):
        return
    chk = _last_check(pkg) or {}
    code = int(chk["solve_exit"]) if chk.get("solve_exit") is not None else 1
    _blind_repair(rewrite, vsession, fmap, text[-4000:], code)
    text = _harness_check(pkg, name="check.blind2")
    if not _agent_checked(pkg):
        raise RuntimeError("blind verifier and reference solution still disagree after "
                           "one repair: " + text[-300:].replace("\n", " | "))


def evolve_agentic(rewrite: layout.RewriteDir, task: dict, job: str, *,
                   observed: str = "", exit_code: int = 1,
                   operator: list[tuple[str, str, str]] | None = None) -> dict:
    """Run one agent session over the rewrite's package, with its container as
    a tool.

    The agent works in the package itself and reaches the task's own
    environment through ./sandbox: a container it can run commands in, run the
    reference solution in, grade, and rebuild fresh. The caller still
    revalidates afterwards; the agent's own pass is not the gate, it is what
    stops the agent from finishing on a rewrite it never ran.

    `job` is one of "harder", "easier", "repair". The attempts are the records
    the loop hardlinked under ``traces/``. Raises on failure; raises Blocked
    when the agent declined.
    """
    _require_codex()
    pkg = rewrite.package
    fmap = _prepare_package(pkg, task)
    if observed:
        (pkg / "run" / "failure.txt").write_text(observed)

    solved = task.get("_solved", 0)
    attempts_n = task.get("_attempts", len(list(rewrite.traces.glob("attempt-*.jsonl"))) or 16)
    # `operator` is the scored shortlist, in score order, each entry
    # (family, operator_id, definition) -- the same order operator_shortlist
    # and pick_operator both return.
    cands = list(operator or [])
    allowed = {op: fam for fam, op, _ in cands}
    seed_size = ts.size_of(task["solve_sh"], task["test_state_py"],
                           "python" if ev._verifier_rel(task).endswith(".py") else "shell")
    blind = job == "harder" and VERIFIER_AUTHOR == "blind"
    prompt = {
        "harder": (_HARDER_JOB_BLIND if blind else _HARDER_JOB).format(
                                     solved=solved, attempts=attempts_n,
                                     candidates=_candidates(cands),
                                     seed_lines=seed_size["solution_lines"],
                                     seed_asserts=seed_size["verifier_asserts"],
                                     min_added=ts.MIN_ADDED, max_added=ts.MAX_ADDED,
                                     max_asserts=ts.MAX_ADDED_ASSERTS),
        "easier": _EASIER_JOB.format(solved=solved, attempts=attempts_n),
        "repair": _REPAIR_JOB.format(exit_code=exit_code),
    }[job] + _traces_spec(rewrite.traces) + _budget(AGENT_TIMEOUT)

    with session(rewrite, "agent", timeout=AGENT_TIMEOUT) as run:
        try:
            p = _run_codex(run, pkg, prompt)
        finally:
            _sandbox_down(pkg)

    vsession = None
    try:
        _check_verdict(pkg)
        _require_checked(pkg)
        if blind:
            vsession, fmap["test_state_py"] = _blind_verifier(rewrite, task, fmap)
            _reconcile_blind(rewrite, vsession, fmap)
        out = _collect(task, pkg, fmap)
    except Blocked:
        raise
    except Exception as exc:
        # A session the classifier stopped left the package half-written,
        # so every check below it fails for that reason rather than for
        # the rewrite's. Say which it was, so the caller starts a fresh
        # session instead of recording the task as unevolvable.
        if cyber_filtered(run.dir):
            raise Filtered(f"the provider's cybersecurity classifier stopped the "
                           f"session ({type(exc).__name__}: {exc})"[:300]) from exc
        raise
    if all(out[key] == task[key] for key in fmap) and not out["_support_changed"]:
        raise RuntimeError(f"agent changed nothing (exit {p.returncode}): "
                           f"{p.stdout[-200:]}")
    if not out["instruction"].strip():
        raise RuntimeError("agent emptied the instruction")
    out["_hint"] = f"agent_{job}"
    out["_agent_validated"] = _agent_checked(pkg)
    out["_session"] = str(run.dir.path)
    if vsession is not None:
        out["_verifier_author"] = "blind"
        out["_verifier_session"] = str(vsession.path)
    if cands:
        # The diversity terms are not a counter -- they are recomputed each
        # round from the operator every accepted rewrite records. A rewrite
        # folded without one is invisible to that scan, so the family balance
        # drifts and nothing reports it. That is why an unreadable
        # declaration fails the session rather than defaulting to the top
        # candidate: a wrong operator on a folded task is worse for the
        # balance than no task, because it is counted.
        declared = (pkg / "run" / "operator.txt")
        chosen = (declared.read_text().strip().split()[0]
                  if declared.exists() and declared.read_text().strip()
                  else "")
        if chosen not in allowed:
            raise RuntimeError(
                f"agent did not declare which axis it used "
                f"(run/operator.txt={chosen!r}, offered={sorted(allowed)})")
        out["_operator"], out["_family"] = chosen, allowed[chosen]
    return out


def _resume_blind(rewrite: layout.RewriteDir, task: dict, observed: str,
                  exit_code: int) -> dict:
    vsession = layout.SessionDir(Path(task.get("_verifier_session") or ""))
    if not vsession.package.is_dir():
        raise RuntimeError(f"no verifier session to resume at {vsession.path}")
    pkg = rewrite.package
    fmap = ev.file_map(task)
    _write_resources(pkg, task)
    _write_pretest(pkg, task)
    fmap["test_state_py"] = _blind_repair(rewrite, vsession, fmap, observed, exit_code)
    text = _harness_check(pkg, name=f"check.resume{time.time_ns() % 100000}")
    if not _agent_checked(pkg):
        raise RuntimeError("blind verifier and reference solution still disagree on resume: "
                           + text[-300:].replace("\n", " | "))
    out = _collect(task, pkg, fmap)
    out["_repaired"] = "codex_resume_verifier"
    out["_agent_validated"] = True
    return out


def resume_agentic(rewrite: layout.RewriteDir, task: dict, observed: str,
                   exit_code: int = 1) -> dict:
    """Continue the session that wrote this task, with the caller's failure.

    The caller rebuilt the package and ran the reference solution against the
    verifier; it failed. A fresh repair session has to rediscover what the
    first one knew -- why the files look the way they do -- from the files
    alone. The first session is on disk (`codex exec resume`), so give it the
    failure instead. Raises on failure; raises Blocked when the agent declined.
    """
    _require_codex()
    pkg = rewrite.package
    if task.get("_verifier_author") == "blind":
        # The caller's oracle failed at the row's size. The session that can
        # answer without seeing the solution is the verifier's; resuming the
        # author instead would hand it the verifier it was kept away from.
        return _resume_blind(rewrite, task, observed, exit_code)
    prior = layout.SessionDir(Path(task.get("_session") or ""))
    if not prior.codex_home.is_dir():
        raise RuntimeError(f"no agent session to resume at {prior.path}")
    sid = _session_id(prior)
    fmap = ev.file_map(task)
    (pkg / "run").mkdir(exist_ok=True)
    _write_resources(pkg, task)
    _write_pretest(pkg, task)
    (pkg / "run" / "failure.txt").write_text(observed or "(no output captured)")
    for stale in ("verdict.txt", "checks.jsonl"):
        (pkg / "run" / stale).unlink(missing_ok=True)
    with session(rewrite, "repair", timeout=AGENT_TIMEOUT, resumes=prior) as run:
        prompt = _REPAIR_JOB.format(exit_code=exit_code) + _budget(AGENT_TIMEOUT)
        try:
            p = _run_codex(run, pkg, prompt, resume=sid)
        finally:
            _sandbox_down(pkg)
    _check_verdict(pkg)
    _require_checked(pkg)
    out = _collect(task, pkg, fmap)
    if all(out[key] == task[key] for key in fmap) and not out["_support_changed"]:
        raise RuntimeError(f"agent changed nothing on resume "
                           f"(exit {p.returncode}): {p.stdout[-200:]}")
    out["_repaired"] = "codex_resume"
    out["_agent_validated"] = _agent_checked(pkg)
    # The repair's codex/ shares the thread's jsonl with the agent's, so a
    # later resume from either finds the same session id.
    out["_session"] = str(run.dir.path)
    return out
