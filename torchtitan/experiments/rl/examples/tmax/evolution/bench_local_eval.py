#!/usr/bin/env python3
"""Score an EvolveBench evalset against a locally served student (vLLM's
OpenAI server), writing the eval directory in the layout tb2_eval_local.sh's
controller leaves, so `tally`, `freeze --eval` and `compare` read it unchanged:

    <out>/launch.json                                    recipe, checkpoint, GPUs
    <out>/validation_rollouts/<task>/g-1-r<i>.jsonl      one record per attempt
    <out>/trainer/validation_traces/step-0/index.json    written when all are done

Each attempt is a training rollout of the row (TMaxRollouter._run_agent_rollout's
steps for a group id >= 0): boot the row's sandbox, start its entrypoint, seed,
capture the integrity baseline, run the harness under the recipe's turns,
context, budget, notice and strict-opening rule, and grade with the row's
verifier only when the attempt submitted. The harness and rollouter code is
the checkout on PYTHONPATH (--student-checkout), recorded as tt_commit.

--stop-on-fail is for screening a slice whose members must solve every
attempt: once a task has a failed attempt its remaining attempts are not
started (launch.json records it). Attempts are issued round-robin over tasks
in rounds of --burst so a failing task is cut after its first burst.

    PYTHONPATH=<student checkout> python bench_local_eval.py --evalset X.jsonl \
        --recipe train-111c-122b --url http://127.0.0.1:8041/v1 --model q122b \
        --checkpoint base-Qwen3.5-122B-A10B --out <base>/evals/<stamp>--<name>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

_EVO = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVO))
import evolve_bench as eb  # noqa: E402

LABEL = "evb122b"


def _gpu_names() -> list[str]:
    out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout
    return [x.strip() for x in out.splitlines() if x.strip()]


class VLLMAdapter:
    """The two adapter calls the harnesses make, against an OpenAI server. The
    prompt opens <think>; the parser sees only what follows </think>, and the
    record keeps the whole completion, as the run's adapter does."""

    def __init__(self, http, url: str, model: str, *, max_model_len: int, turn_max_tokens: int,
                 temperature: float, top_p: float) -> None:
        self.http, self.url, self.model = http, url.rstrip("/") + "/chat/completions", model
        self.max_model_len, self.turn_max_tokens = max_model_len, turn_max_tokens
        self.temperature, self.top_p = temperature, top_p
        self.completions: dict[str, list[str]] = {}
        self.prompts: dict[str, list[str]] = {}
        self.usage: dict[str, list[tuple[int, int]]] = {}

    def session_max_tokens(self, sid: str) -> int:
        return self.turn_max_tokens

    async def complete(self, sid: str, payload: dict) -> dict:
        import aiohttp

        msgs = [{"role": m["role"], "content": m["content"]} for m in payload["messages"]]
        self.prompts.setdefault(sid, []).append(msgs[-1]["content"])
        body = {"model": self.model, "messages": msgs, "temperature": self.temperature,
                "top_p": self.top_p, "max_tokens": self.turn_max_tokens}
        msg = ""
        for attempt in range(10):
            try:
                async with self.http.post(self.url, json=body) as r:
                    data = await r.json(content_type=None)
                    status = r.status
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                msg = f"{type(e).__name__}: {e}"
                await asyncio.sleep(min(2 ** attempt, 30))
                continue
            if status == 200:
                break
            msg = json.dumps(data)[:400]
            if "maximum context length" in msg or ("max_tokens" in msg and "too large" in msg) \
                    or "max_completion_tokens" in msg:
                used = _prompt_tokens(msg, self.max_model_len)
                left = self.max_model_len - 1 - used
                if left < 256:
                    # Nothing fits: the context wall, as the run's adapter reports it.
                    return {"content": [{"type": "text", "text": ""}], "stop_reason": "max_tokens",
                            "usage": {"input_tokens": self.max_model_len, "output_tokens": 0}}
                body["max_tokens"] = min(self.turn_max_tokens, left)
                continue
            await asyncio.sleep(min(2 ** attempt, 30))
        else:
            raise RuntimeError(f"vllm failed: {msg}")
        ch = data["choices"][0]
        raw = ch["message"].get("content") or ""
        think, sep, after = raw.partition("</think>")
        text = after.lstrip() if sep else raw
        self.completions.setdefault(sid, []).append(f"<think>{think}</think>\n{text}" if sep else raw)
        u = data.get("usage") or {}
        self.usage.setdefault(sid, []).append((u.get("prompt_tokens", 0), u.get("completion_tokens", 0)))
        return {"content": [{"type": "text", "text": text}],
                "stop_reason": "max_tokens" if ch.get("finish_reason") == "length" else "end_turn",
                "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}}


def _prompt_tokens(msg: str, max_model_len: int) -> int:
    nums = [int(x) for x in re.findall(r"(\d{4,})", msg)]
    cands = [n for n in nums if n < max_model_len * 2 and n != max_model_len]
    return max(cands) if cands else max_model_len


async def rollout(R, terminus, rr, sample, idx: int, adapter: VLLMAdapter, out: Path, cfg: dict) -> dict:
    """One attempt, the training path's steps; returns its index entry."""
    from torchtitan.experiments.rl.harness import AgentTask, SandboxIssueTracker, SandboxLogContext

    sid = f"{sample.instance_id}-g-1-r{idx}"
    exec_timing: dict = {"exec_secs": 0.0, "exec_n": 0}
    tracker = SandboxIssueTracker(SandboxLogContext(instance_id=sample.instance_id, group_id=-1, rollout_id=idx))
    declared = sample.agent_timeout_sec
    budget = cfg["time_budget"] if declared is None else max(int(declared), cfg["budget_floor"])
    vdecl = sample.verifier_timeout_sec
    verifier_sec = cfg["eval_timeout"] if vdecl is None else max(int(vdecl), cfg["eval_timeout"])
    t0 = time.time()
    head = {"task": sample.instance_id, "rev": sample.rev, "run": out.name, "group": -1, "rollout": idx,
            "reward": 0.0, "status": "error", "finish_reason": "error", "submitted": False,
            "infra_failed": False, "agent": cfg["agent"], "budget_sec": budget, "sandbox": {}, "error": ""}
    run = None
    stage = "setup"
    try:
        if sample.tmax.get("separate_verifier"):
            raise RuntimeError("separate_verifier rows are not supported by the local eval")
        async with asyncio.timeout(budget + verifier_sec + 1800):
            async with R.boot_agent_sandbox(
                sample.image, dockerfile=sample.dockerfile, build_context=sample.build_context,
                install_claude=False, cpu=sample.daytona_cpu, memory=sample.daytona_mem_gb,
                disk_gb=sample.daytona_disk_gb, issue_tracker=tracker,
                labels={"task": sample.instance_id, "purpose": LABEL, "eval": out.name, "rollout": str(idx)},
                **getattr(sample, "boot_kwargs", {}),
            ) as sandbox:
                head["sandbox"] = {"id": sandbox.sandbox_id}
                with open(out / "sandboxes.txt", "a") as f:
                    f.write(f"{sandbox.sandbox_id}\t{sample.instance_id}\t{idx}\n")
                root = R._RootSandbox(sandbox, exec_timing)
                if sample.entrypoint:
                    await R._start_entrypoint(root, sample.entrypoint, workdir=sample.workdir)
                await R.seed_workspace(root, sample.tmax)
                await R._set_debconf_noninteractive(root)
                baseline = await R.capture_baseline(root, sample.tmax, workdir=sample.workdir,
                                                    timeout=verifier_sec or 120)
                stage = "agent"
                run = await terminus.terminus_agent(AgentTask(
                    sandbox=root, instruction=sample.problem_statement, session_id=sid, adapter=adapter,
                    time_budget_sec=budget, workdir=sample.workdir, max_context_tokens=cfg["max_context"],
                    max_turns=cfg["max_turns"], strict_format=cfg["strict_format"],
                    strict_opening=cfg["strict_opening"], strict_truncation=cfg["strict_truncation"],
                    context_notice=cfg["context_notice"], format_streak_limit=cfg["format_streak"],
                ))
                submitted = run.submitted is not False
                head.update(finish_reason=run.finish_reason, submitted=submitted, status="completed",
                            format_errors=run.format_errors, terminal_events=run.terminal_events,
                            exec=run.exec_trace, harness_turns=run.turns)
                if submitted:
                    stage = "verifier"
                    diag: dict = {}
                    head["reward"] = float(await R.grade_tmax(
                        sandbox, sample.tmax, workdir=sample.workdir, timeout_sec=verifier_sec,
                        baseline_digests=baseline, diagnostics=diag))
                    head["verifier"] = {k: v for k, v in diag.items() if k != "output"}
    except Exception as e:  # noqa: BLE001 -- an attempt's failure is recorded, never fatal
        head["error"] = f"{type(e).__name__}: {e}"[:500]
        head["failure"] = {"stage": stage}
        # An exception outside the agent's own loop is the harness's or the
        # platform's: the attempt says nothing about the task.
        if run is None or stage == "verifier":
            head["infra_failed"] = True
    head["secs"] = round(time.time() - t0, 1)
    comps, prompts = adapter.completions.pop(sid, []), adapter.prompts.pop(sid, [])
    usage = adapter.usage.pop(sid, [])
    head["turns"] = len(comps)
    head["tokens"] = {"prompt_last": usage[-1][0] if usage else 0, "completion": sum(u[1] for u in usage)}
    turns = [rr.turn_line(i + 1, c, prompts[i + 1] if i + 1 < len(prompts) else "") for i, c in enumerate(comps)]
    rel = f"validation_rollouts/{sample.instance_id}/g-1-r{idx}.jsonl"
    rr.write_record(out / rel, head, turns)
    state = "PASS" if head["reward"] >= 1.0 else "FAIL"
    print(json.dumps({k: head.get(k) for k in ("task", "rollout", "reward", "finish_reason", "turns", "secs",
                                                "infra_failed", "error")}), flush=True)
    return {"task": sample.instance_id, "state": state, "infra_failed": bool(head["infra_failed"]),
            "finish_reason": head.get("finish_reason"), "record": rel}


def _cfg(recipe: dict[str, str]) -> dict:
    g = recipe.get
    return {
        "k": int(g("SWE_TB2_VAL_K")),
        "max_model_len": int(g("SWE_TMAX_CONTEXT")),
        "max_context": int(g("SWE_MAX_CONTEXT_LEN")),
        "turn_max_tokens": int(g("TMAX_TURN_MAX_TOKENS")),
        "max_turns": int(g("TMAX_TERMINUS_MAX_TURNS")),
        "time_budget": int(g("SWE_TIME_BUDGET_SEC")),
        "budget_floor": int(g("SWE_AGENT_TIMEOUT_FLOOR_SEC") or g("SWE_EVAL_BUDGET_FLOOR_SEC")),
        "eval_timeout": int(g("TMAX_EVAL_TIMEOUT_SEC") or 600),
        "strict_opening": g("SWE_EVAL_STRICT_OPENING") == "1",
        "strict_format": g("SWE_EVAL_STRICT_FORMAT") == "1",
        "strict_truncation": g("SWE_EVAL_STRICT_TRUNCATION") == "1",
        "context_notice": g("SWE_EVAL_CONTEXT_NOTICE") == "1",
        "format_streak": int(g("TMAX_FORMAT_STREAK_LIMIT") or 0),
        "temperature": float(g("SWE_TB2_VAL_TEMPERATURE")),
        "top_p": float(g("SWE_TB2_VAL_TOP_P")),
    }


async def main_async(a: argparse.Namespace) -> None:
    recipe = eb.STUDENT_RECIPES[a.recipe]
    # terminus.py reads its knobs at import; the recipe is the environment.
    for k, v in recipe.items():
        if k in os.environ and os.environ[k] != v:
            raise SystemExit(f"the environment sets {k}={os.environ[k]}, which {a.recipe} pins to {v}")
        os.environ[k] = v
    import aiohttp
    from dataclasses import replace

    from torchtitan.experiments.rl.examples.tmax import rollout_record as rr
    from torchtitan.experiments.rl.examples.tmax import rollouter as R
    from torchtitan.experiments.rl.examples.tmax.data import _parse_sample_row, bake_tmux_dockerfile
    from torchtitan.experiments.rl.harness.agents import terminus

    cfg = _cfg(recipe)
    cfg["agent"] = a.harness
    if a.harness != "terminus":
        raise SystemExit("the local eval drives terminus only")
    evalset = a.evalset.resolve()
    rows = eb._rows(evalset)
    samples = []
    for row in rows:
        s = _parse_sample_row(row)
        if recipe.get("SWE_TB2_BAKE_TMUX") == "1":
            s = replace(s, dockerfile=bake_tmux_dockerfile(s.dockerfile, s.image))
        samples.append(s)
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    student = Path(a.student_checkout).resolve()
    launch = {
        "env": {**recipe, "SWE_BENCH_RECIPE": a.recipe, "SWE_TB2_DATA": str(evalset), "TMAX_AGENT": a.harness,
                "SWE_ROLLOUT_CONCURRENCY": str(a.concurrency),
                "SWE_BENCH_LOCAL_EVAL": "1", "SWE_BENCH_STOP_ON_FAIL": "1" if a.stop_on_fail else "0",
                "TMAX_TERMINUS_UNFREEZE": os.environ.get("TMAX_TERMINUS_UNFREEZE", "1")},
        "evalset_sha256": eb.layout.sha256_file(evalset),
        "checkpoint": a.checkpoint,
        "tt_commit": eb._git(student, "rev-parse", "HEAD"),
        "bench_commit": eb._bench_commit(),
        "gpu_names": _gpu_names(),
        "num_generators": a.generators,
        "serving": {"url": a.url, "model": a.model, "layout": a.serving_layout},
        "host": socket.gethostname(),
        "started": eb.layout.stamp(),
    }
    lp = out / "launch.json"
    if lp.exists():
        prev = json.load(open(lp))
        if {k: prev.get(k) for k in ("evalset_sha256", "checkpoint", "tt_commit")} != \
                {k: launch.get(k) for k in ("evalset_sha256", "checkpoint", "tt_commit")}:
            raise SystemExit(f"{out} holds another eval")
        launch["started"] = prev["started"]
    eb.layout.write_json_atomic(lp, launch)

    # Resume: an attempt whose record exists is done.
    done: dict[tuple[str, int], dict] = {}
    for f in out.glob("validation_rollouts/*/g-1-r*.jsonl"):
        h = rr.read_header(f)
        rel = str(f.relative_to(out))
        done[(h["task"], int(h["rollout"]))] = {
            "task": h["task"], "state": "PASS" if (h.get("reward") or 0) >= 1.0 else "FAIL",
            "infra_failed": bool(h.get("infra_failed")), "finish_reason": h.get("finish_reason"), "record": rel}
    failed = {t for (t, _), e in done.items() if e["state"] == "FAIL" and not e["infra_failed"]}
    by_id = {s.instance_id: s for s in samples}
    order = [(s.instance_id, i) for b0 in range(0, cfg["k"], a.burst) for s in samples
             for i in range(b0, min(b0 + a.burst, cfg["k"]))]
    todo = [x for x in order if x not in done]
    sem = asyncio.Semaphore(a.concurrency)
    timeout = aiohttp.ClientTimeout(total=3600)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True, limit=0),
                                     timeout=timeout) as http:
        adapter = VLLMAdapter(http, a.url, a.model, max_model_len=cfg["max_model_len"],
                              turn_max_tokens=cfg["turn_max_tokens"], temperature=cfg["temperature"],
                              top_p=cfg["top_p"])

        async def one(tid: str, i: int):
            async with sem:
                if a.stop_on_fail and tid in failed:
                    return None
                e = await rollout(R, terminus, rr, by_id[tid], i, adapter, out, cfg)
                if e["state"] == "FAIL" and not e["infra_failed"]:
                    failed.add(tid)
                done[(tid, i)] = e
                return e

        await asyncio.gather(*(one(t, i) for t, i in todo))
    index = [done[x] for x in order if x in done]
    eb.layout.write_json_atomic(out / eb.INDEX, index)
    launch["finished"] = eb.layout.stamp()
    eb.layout.write_json_atomic(lp, launch)
    print(f"{out}: {len(index)} attempts over {len({e['task'] for e in index})} tasks")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evalset", type=Path, required=True)
    ap.add_argument("--recipe", required=True, choices=sorted(eb.STUDENT_RECIPES))
    ap.add_argument("--harness", default="terminus")
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--student-checkout", required=True)
    ap.add_argument("--serving-layout", default="")
    ap.add_argument("--generators", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--concurrency", type=int, default=48)
    ap.add_argument("--burst", type=int, default=12)
    ap.add_argument("--stop-on-fail", action="store_true")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
