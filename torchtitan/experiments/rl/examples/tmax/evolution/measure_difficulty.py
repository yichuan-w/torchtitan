#!/usr/bin/env python3
"""Did the evolve actually move the task's difficulty? Re-solve and compare.

The loop folds a re-tuned task back and moves on; nothing measures whether the
task's difficulty actually moved toward target. This closes that loop directly:
take recently-evolved tasks, re-solve the RE-TUNED package pass@k, and compare
to the before-solve-rate the training signal recorded.

  before (from the signal):  easier direction was 0/16, harder was 16/16
  after  (measured here):    the re-tuned package's pass@k now

  success = easier moved 0 -> solvable (>0); harder moved 16/16 -> below full.

Runs at MAX concurrency (auto_delete=15min keeps the account clean regardless).

  measure_difficulty.py --limit 30 --concurrency 200 --out results/difficulty.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

os.environ.setdefault(
    "TRL_EXTRA_POOL",
    str(Path(os.environ.get("TRL_BASE", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))
        / "evolution/retuned"))

import solve_daytona as sd  # noqa: E402  (imports after TRL_EXTRA_POOL is set)

BASE = sd.BASE
log = logging.getLogger("measure_difficulty")


def recent_evolved(limit: int) -> list[dict]:
    """Tasks with a re-tuned package on disk and a recorded before-rate.

    Reads consumed signals (each has task_id, solved, total, direction), keeps
    the LATEST per task, and only those whose retuned package exists (so there
    is an evolved artifact to re-solve)."""
    retuned = BASE / "evolution/retuned"
    latest: dict[str, dict] = {}
    sigs = sorted((BASE / "evolution/consumed").glob("*.json"),
                  key=os.path.getmtime)
    for p in sigs:
        try:
            s = json.load(open(p))
        except Exception:  # noqa: BLE001
            continue
        tid = s.get("task_id")
        if not tid:
            continue
        if not (retuned / tid / "instruction.md").exists():
            continue
        latest[tid] = {"task_id": tid, "before_solved": s.get("solved"),
                       "before_total": s.get("total"),
                       "direction": s.get("direction")}
    rows = list(latest.values())
    return rows[-limit:] if limit else rows


async def run(args) -> None:
    rows = recent_evolved(args.limit)
    by_id = {r["task_id"]: r for r in rows}
    print(f"evolved tasks with a re-tuned package: {len(rows)}")
    sem = asyncio.Semaphore(args.concurrency)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    results = await asyncio.gather(*[
        sd.solve_task(r["task_id"], args.attempts, args.max_turns, sem)
        for r in rows])
    moved = same = 0
    with open(out, "w") as f:
        for res in results:
            tid = res["task_id"]
            b = by_id.get(tid, {})
            after = res.get("solved")
            graded = res.get("graded")
            direction = b.get("direction")
            # success: easier -> now solvable; harder -> now below full
            ok = None
            if graded:
                if direction == "easier":
                    ok = (after or 0) > 0
                elif direction == "harder":
                    ok = (after or 0) < graded
            rec = {**b, "after_solved": after, "after_graded": graded,
                   "after_pass_at_k": res.get("pass_at_k"),
                   "moved_toward_target": ok}
            f.write(json.dumps(rec) + "\n")
            if ok is True:
                moved += 1
            elif ok is False:
                same += 1
            log.info("%s [%s] before=%s/%s after=%s/%s -> moved=%s", tid,
                     direction, b.get("before_solved"), b.get("before_total"),
                     after, graded, ok)
    n = moved + same
    print("\n=== difficulty movement ===")
    print(f"measured (graded): {n}")
    if n:
        print(f"moved toward target: {moved}/{n} ({moved/n:.0%})")
        print(f"did NOT move (evolve ineffective on these): {same}/{n}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--concurrency", type=int, default=200)
    ap.add_argument("--out", default=str(BASE / "results/difficulty.jsonl"))
    ap.add_argument("--log", default=str(BASE / "logs/measure_difficulty.log"))
    args = ap.parse_args()
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()])
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
