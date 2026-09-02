#!/usr/bin/env python3
"""Fold the re-judged verdicts back into the dataset, and say what moved.

Two runner limits were being recorded as facts about tasks: a container started
with its ENTRYPOINT suppressed, and a heredoc Docker's parser would not accept.
Both are fixed in `docker_validate.py`; this reads what the fixed runner said
and updates `reward_verdict` for the tasks it re-judged.

Nothing else changes. Membership of the shipped subset was decided by a
different measurement — `tests/test.sh` exiting 0 under udocker — and is not
touched here, so the 1,353 stay 1,353 and only their verdicts move.

Two columns are added, because a verdict is only checkable if the environment
that produced it is on the record:

  `run_mode`         entrypoint | entrypoint_bypassed | (null, never re-judged)
  `dockerfile_repaired`  whether the heredoc spacing had to be closed to build

Writes a timestamped backup first. Usage:
  apply_recovery.py --parquet data/seed-dataset-clean/metadata/tasks.parquet \
      --results results/recover/recover_v1.jsonl [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import time
from pathlib import Path

import pandas as pd

VERDICT = {"pass": "pass", "fail": "fail"}


def verdict_of(rec: dict, previous: str) -> str:
    """What the run established, which is not always a verdict.

    `pass` and `fail` come from the reward file and supersede whatever was
    there. Everything else — a build that never completed, a container that
    would not start, a grader that wrote nothing — established nothing, so the
    previous verdict stands on the evidence it already had.

    This is the same rule the rest of this work turns on, applied to ourselves:
    a runner that could not build a task has said something about the runner. It
    matters concretely — one task here built and passed in an earlier run, then
    failed to build in this one against an end-of-life archive that answered 403
    after we sent it eighteen builds at once. Writing that down as `unknown`
    would record our own request rate as a fact about the task.
    """
    return VERDICT.get(rec.get("status"), previous)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--results", required=True, nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    parquet = Path(args.parquet)
    df = pd.read_parquet(parquet)

    # Last verdict wins, as the card says: a task seen passing and later failing
    # is not one whose reference solution reliably satisfies its verifier.
    latest: dict[str, dict] = {}
    for path in args.results:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                prev = latest.get(r["task_id"])
                if prev is None or r.get("t_start", 0) >= prev.get("t_start", 0):
                    latest[r["task_id"]] = r

    before = dict(zip(df["task_id"], df["reward_verdict"]))
    moves: collections.Counter = collections.Counter()
    new_verdict, run_mode, repaired = [], [], []
    for tid, old in zip(df["task_id"], df["reward_verdict"]):
        rec = latest.get(tid)
        if rec is None:
            new_verdict.append(old)
            run_mode.append(None)
            repaired.append(False)
            continue
        v = verdict_of(rec, old)
        new_verdict.append(v)
        run_mode.append(rec.get("run_mode"))
        repaired.append(bool(rec.get("repairs")))
        moves[(old, v)] += 1

    df["reward_verdict"] = new_verdict
    df["run_mode"] = run_mode
    df["dockerfile_repaired"] = repaired

    print(f"re-judged {len(latest)} tasks, {len(df)} in the subset\n")
    print("verdict moves (old -> new):")
    for (old, new), n in sorted(moves.items(), key=lambda x: -x[1]):
        mark = "  " if old == new else "->"
        print(f"  {mark} {old:8s} -> {new:8s}  {n:4d}")
    print("\nsubset totals:")
    for k, n in collections.Counter(before.values()).most_common():
        after = int((df["reward_verdict"] == k).sum())
        print(f"  {k:8s} {n:4d} -> {after:4d}  ({after - n:+d})")
    print("\nenvironment the new verdicts came from:")
    for k, n in collections.Counter(m for m in run_mode if m).most_common():
        print(f"  {k:22s} {n:4d}")
    print(f"  dockerfile repaired    {sum(repaired):4d}")

    if args.dry_run:
        print("\ndry run, nothing written")
        return
    backup = parquet.with_suffix(
        f".parquet.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(parquet, backup)
    df.to_parquet(parquet, index=False)
    print(f"\nbacked up to {backup.name}, wrote {parquet}")


if __name__ == "__main__":
    main()
