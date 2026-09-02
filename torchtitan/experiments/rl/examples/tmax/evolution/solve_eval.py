#!/usr/bin/env python3
"""Ask a solver to do the tasks, which is the thing validation never asked.

Validation established that each task is internally consistent: the environment
builds, the reference solution runs, and the verifier grades it. That says the
task is well-formed. It says nothing about whether a task is worth training on —
a task every model solves on the first try teaches nothing, and one no model ever
solves teaches nothing either. Only a solver can tell those apart, and none has
been run over this corpus.

This is also RST's own convention, so the numbers land next to theirs: k samples
per task in a sandbox, reported as pass@k.

What the container does and does not hold matters more here than anywhere else in
this repo:

  * `solution/` is never staged. It is the answer.
  * `tests/` is staged after the solver stops, never before, or the verifier is
    readable by the thing being tested.
  * each attempt gets a fresh container, so attempt 2 does not inherit attempt
    1's progress and quietly turn pass@k into "k turns".

Usage:
  solve_eval.py --tar chunk000.tar --ids ids.txt --results out.jsonl \\
      --attempts 1 --max-turns 25 [--workers 3] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import docker_validate as dv
import synth_client as llm
import synth_loop as sl

log = logging.getLogger("solve-eval")


def classify(attempt: dict) -> str:
    """What one attempt established, which is often not "the model failed".

    A pilot of ten tasks produced four without a passing grade, and only one of
    the four was the solver failing. The other three were the harness and the
    provider: a request the content filter refused as a possible cybersecurity
    risk, so no turn ever ran, and two containers that were gone by grading time
    because the solver had killed the process the container is built around.
    Counting those as failures is the same mistake this corpus has been through
    four times already — a zero that describes the runner being read as a fact
    about the task.
    """
    reward, why = attempt.get("reward"), attempt.get("why") or ""
    if reward == "1":
        return "solved"
    if reward == "0":
        return "failed"
    if "flagged for possible cybersecurity" in why or "HTTP 400" in why:
        return "refused_by_provider"
    if "No such container" in (attempt.get("test_tail") or ""):
        return "container_lost"
    if "container gone at turn" in why or "container would not start" in why:
        return "container_lost"
    if why.startswith("llm:"):
        return "solver_error"
    return "ungraded"


def solve_one(tar_path: Path, tid: str, work_root: Path, attempts: int,
              max_turns: int, build_attempts: int,
              keep_trace: bool = False) -> dict:
    rec: dict = {"task_id": tid, "t_start": time.time(), "attempts": []}
    work = work_root / tid
    image = f"slv-{tid.lower()}"
    try:
        if dv.free_gb() < dv.PRUNE_BELOW_GB:
            dv.sh(["docker", "builder", "prune", "-f"], 600)
        if dv.free_gb() < dv.MIN_FREE_GB:
            return {**rec, "status": "aborted",
                    "why": f"free disk {dv.free_gb():.1f}G"}

        with tarfile.open(tar_path) as tf:
            dv.extract(tf, tid, work)
        env_dir = work / "environment"
        dockerfile = env_dir / "Dockerfile"
        if not dockerfile.exists():
            return {**rec, "status": "skipped", "why": "no Dockerfile"}
        fixed, n = dv.repair_heredoc_spacing(
            dockerfile.read_text(encoding="utf-8", errors="surrogateescape"))
        if n:
            dockerfile.write_text(fixed, encoding="utf-8",
                                  errors="surrogateescape")
            rec["repairs"] = {"heredoc_spacing": n}

        t0 = time.time()
        rc, out, tries = dv.build_with_retry(env_dir, image, build_attempts,
                                             dv.BUILD_TIMEOUT)
        rec["build_s"] = round(time.time() - t0, 1)
        rec["build_attempts"] = tries
        if rc != 0:
            return {**rec, "status": "build_failed",
                    "build_tail": dv.tail_signal(out, 900)}

        instruction = (work / "instruction.md").read_text(
            encoding="utf-8", errors="replace")
        for i in range(attempts):
            mark = dict(llm.USAGE)
            a = sl.agent_attempt(work, image, tid, instruction, max_turns, i)
            a["usage"] = llm.usage_since(mark)
            # The transcript is large, so a pure pass@k measurement drops it.
            # When this run exists to feed the feedback loop, the transcript is
            # the point — it is the trajectory a simplify hint is drawn from —
            # so keep_trace keeps it.
            if not keep_trace:
                a.pop("transcript", None)
            a["outcome"] = classify(a)
            # A lost container is the harness losing the attempt, not the solver
            # failing it, so it is worth one fresh try. Once — a task the solver
            # reliably wrecks would otherwise loop, and that behaviour is itself
            # a finding rather than something to retry away.
            if a["outcome"] == "container_lost":
                mark = dict(llm.USAGE)
                b = sl.agent_attempt(work, image, tid, instruction, max_turns,
                                     f"{i}r")
                b["usage"] = llm.usage_since(mark)
                if not keep_trace:
                    b.pop("transcript", None)
                b["outcome"] = classify(b)
                b["retry_of"] = a["outcome"]
                a = b
            rec["attempts"].append(a)

        rec["rewards"] = [a.get("reward") for a in rec["attempts"]]
        rec["outcomes"] = [a["outcome"] for a in rec["attempts"]]
        graded = [a for a in rec["attempts"]
                  if a["outcome"] in ("solved", "failed")]
        solved = sum(1 for a in graded if a["outcome"] == "solved")
        rec["solved"] = solved
        rec["graded"] = len(graded)
        # Over the attempts that produced a verdict, not over the attempts that
        # were started. An attempt the content filter refused, or one whose
        # container died before it could be graded, says nothing about the task
        # and averaging it in as a failure understates every rate here.
        rec["pass_at_k"] = solved / len(graded) if graded else None
        rec["status"] = ("solved" if solved else
                         "unsolved" if graded else "ungraded")
        return rec
    except Exception as e:  # noqa: BLE001
        return {**rec, "status": "error", "why": f"{type(e).__name__}: {e}"[:300]}
    finally:
        rec["t_end"] = time.time()
        dv.sh(["docker", "rmi", "-f", image], 300)
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tar", required=True, nargs="+")
    ap.add_argument("--ids", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--work", default="./work-solve")
    ap.add_argument("--attempts", type=int, default=1)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--build-attempts", type=int, default=3)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--keep-trace", action="store_true",
                    help="store each attempt's transcript, the trajectory the "
                         "feedback loop reads; off for a pure pass@k measurement")
    args = ap.parse_args()

    results = Path(args.results)
    results.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(results.with_suffix(".log")),
                  logging.StreamHandler()])

    done = set()
    if results.exists():
        for line in results.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") in ("solved", "unsolved"):
                    done.add(r["task_id"])

    ids = [t.strip() for t in Path(args.ids).read_text().split() if t.strip()]
    todo = [t for t in ids if t not in done]
    if args.limit:
        todo = todo[:args.limit]
    index = dv.index_tars(args.tar, results.with_suffix(".tarindex.json"))
    todo = [t for t in todo if t in index]
    log.info("%d ids, %d already run, %d to solve, %d attempts each, model %s",
             len(ids), len(done), len(todo), args.attempts, llm.MODEL)

    work_root = Path(args.work)
    work_root.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    solved_total = graded_total = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex, \
         open(results, "a") as fh:
        futs = {ex.submit(solve_one, Path(index[t]), t, work_root,
                          args.attempts, args.max_turns,
                          args.build_attempts, args.keep_trace): t for t in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            if rec["status"] in ("solved", "unsolved"):
                graded_total += 1
                solved_total += 1 if rec["solved"] else 0
            for a in rec.get("attempts", []):
                outcomes[a["outcome"]] = outcomes.get(a["outcome"], 0) + 1
            u = sum((a.get("usage") or {}).get("prompt_tokens", 0)
                    for a in rec.get("attempts", []))
            o = sum((a.get("usage") or {}).get("completion_tokens", 0)
                    for a in rec.get("attempts", []))
            log.info("[%d/%d] %s -> %s (pass@k=%s over %s graded, "
                     "%dk in %dk out) | tasks solved %d/%d | attempts %s",
                     n, len(todo), rec["task_id"], rec["status"],
                     rec.get("pass_at_k"), rec.get("graded"),
                     u // 1000, o // 1000,
                     solved_total, graded_total, outcomes)
            if rec["status"] == "aborted":
                log.error("aborting: disk below floor")
                break
    log.info("RUN DONE: tasks %s | solved %d of %d graded | attempts %s",
             counts, solved_total, graded_total, outcomes)


if __name__ == "__main__":
    main()
