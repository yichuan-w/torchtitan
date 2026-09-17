#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""How many TMax tasks survive, under each rule for what counts as admissible.

The V8 audit hands back one label per task, and only `tier: PASS` is admitted
today — 494 of 1969 decided tasks. That is a policy choice, not a measurement,
and the audit already recorded everything needed to make a different one: which
defect was found, which direction the reward is wrong in, and how cheap the
exploit is. So the question "how much corpus would a looser rule buy" is
answerable from the existing rows, with no re-judging.

The answer is dominated by one number. 1161 of those tasks are `accepts-wrong`
at `repro_cost: simple` — a wrong submission earns full reward with no task
understanding, typically five generic shell statements. Admit them and the
corpus roughly doubles; admit them and the policy learns to look for the
shortcut, which is a habit that carries to tasks that do not have one. Every
loose rule that looks attractive here is attractive because of that bucket, so
print it separately rather than letting it hide inside a total.

The other bucket worth naming is `rejects-right`: 240 tasks where a faithful
solution is scored 0. It costs a run twice over — no gradient from an all-fail
group, and the reward actively punishes correct behaviour — and it is invisible
to anyone filtering only for "can this be cheated".

Rows come from the V8 re-judge, on the `main` branch of this repository at
`data_curation/tmax/v8/results/*.rows.jsonl`. They are not on this branch:
copy them out with `git show main:<path>` or pass `--rows` at a checkout of
`main`. Tasks judged more than once (the five regression guards, re-judged in
every run) are counted once, at their first verdict.

Usage: tmax_admission_policy.py --rows '<dir-or-glob>/*.rows.jsonl'
       tmax_admission_policy.py --rows '...' --list no-free-lunch > kept.ids
"""
from __future__ import annotations

import argparse
import collections
import glob
import json


def load(pattern: str) -> dict[str, dict]:
    """task_id -> first verdict for it, across every matching rows file."""
    rows: dict[str, dict] = {}
    for path in sorted(glob.glob(pattern)):
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows.setdefault(rec["task_id"], rec)
    return rows


def defect(rec: dict, key: str) -> bool:
    return bool((rec.get("defects") or {}).get(key))


def broken(rec: dict) -> bool:
    """Wrong beyond argument: the reward is reachable without doing the task,
    or the task cannot be done at all."""
    return (
        rec.get("verdict")
        in {
            "ANSWER-LEAK",
            "MUTABLE-GROUND-TRUTH",
            "INSTR-ENV-MISMATCH",
            "TASK-TRIVIAL",
            "OTHER",
        }
        or defect(rec, "reachable_reference")
        or defect(rec, "preplaced_value")
        or defect(rec, "mutable_ground_truth")
        or defect(rec, "nondeterministic_reward")
    )


def rejects_right(rec: dict) -> bool:
    """A faithful solution is scored 0."""
    return (
        rec.get("soundness") == "rejects-right"
        or rec.get("mismatch_direction") in {"faithful_solution_rejected", "both"}
        or defect(rec, "rejects_correct_variant")
    )


def free_lunch(rec: dict) -> bool:
    """Full reward for a wrong submission that needs no task understanding."""
    return rec.get("soundness") == "accepts-wrong" and rec.get("repro_cost") == "simple"


# Each rule answers "is this task admissible", most conservative first. The
# names say what the rule keeps out, because that is what a reader has to judge.
POLICIES = {
    "pass-only": lambda r: r.get("tier") == "PASS",
    "no-broken": lambda r: not broken(r),
    "no-broken+no-rejects-right": lambda r: not broken(r) and not rejects_right(r),
    "no-free-lunch": lambda r: not broken(r)
    and not rejects_right(r)
    and not free_lunch(r),
}

BUCKETS = {
    "broken (leak / mutable oracle / unbuildable / pays for nothing)": broken,
    "rejects-right (a correct solution scores 0)": rejects_right,
    "free lunch (accepts-wrong at simple cost)": free_lunch,
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", required=True, help="glob for the V8 *.rows.jsonl files")
    ap.add_argument(
        "--list",
        choices=sorted(POLICIES),
        help="print the kept task ids instead of the table",
    )
    args = ap.parse_args()

    rows = load(args.rows)
    decided = [r for r in rows.values() if r.get("tier")]
    skipped = len(rows) - len(decided)

    if args.list:
        for task_id in sorted(r["task_id"] for r in decided if POLICIES[args.list](r)):
            print(task_id)
        return

    print(
        f"{len(rows)} unique tasks; {len(decided)} decided, {skipped} skipped (no tier)\n"
    )
    print(f"{'admission rule':44s} {'kept':>6s} {'share':>7s}")
    for name, keep in POLICIES.items():
        kept = sum(1 for r in decided if keep(r))
        print(f"{name:44s} {kept:6d} {kept / len(decided) * 100:6.1f}%")

    print("\nwhat each rule excludes (buckets overlap):")
    for name, hit in BUCKETS.items():
        print(f"  {name:62s} {sum(1 for r in decided if hit(r)):5d}")

    loosest = [r for r in decided if POLICIES["no-free-lunch"](r)]
    tiers = collections.Counter(r["tier"] for r in loosest)
    print("\nno-free-lunch keeps these, by the tier V8 gave them:")
    for tier, n in tiers.most_common():
        print(f"  {tier:16s} {n}")

    # Anything admitted still rests on the judge having caught the leaks, and on
    # the guards it did not: two of the five known-leak regression tasks were
    # PASSed in 3 of 5 runs. These two flags are where that shows up first.
    for key in ("reachable_reference", "preplaced_value"):
        print(
            f"\n{key} true in {sum(1 for r in decided if defect(r, key))} decided tasks — floor-check these before training"
        )


if __name__ == "__main__":
    main()
