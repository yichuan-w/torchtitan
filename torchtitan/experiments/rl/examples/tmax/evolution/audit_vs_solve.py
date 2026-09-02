#!/usr/bin/env python3
"""Cross the solvability audit against what a solver actually managed.

The audit is a model reading four files and saying whether an agent could do the
task from the instruction alone. That is a judgement, and nothing so far has
checked it against anything — so a verdict of "environment" on 44% of the corpus
could mean the corpus needs the network, or it could mean the auditor flags any
`apt-get` it sees.

Measured pass@5 is the check available. It is not ground truth for solvability
either — the solver has its own failures — but the two are independent, so where
they disagree is where at least one of them is wrong, and how strongly they agree
is how much the audit is worth.

What the table says, read left to right: if "environment" tasks are solved as
often as "solvable" ones, the verdict carries no information and the gate built
on it is rejecting tasks at random.

Usage: audit_vs_solve.py --audit results/seed_solvability.jsonl \\
           --solve results/solve_all861.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def read(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def rate(rec: dict) -> float | None:
    """pass@k from whichever shape the solve file uses."""
    if isinstance(rec.get("pass_at_k"), (int, float)):
        return float(rec["pass_at_k"])
    rewards = rec.get("rewards") or []
    graded = [r for r in rewards if str(r) in ("0", "1")]
    return sum(1 for r in graded if str(r) == "1") / len(graded) if graded else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--solve", required=True)
    ap.add_argument("--key", default="task_id")
    args = ap.parse_args()

    verdicts = {r.get(args.key): r.get("verdict") for r in read(args.audit)}
    solves = {}
    for r in read(args.solve):
        v = rate(r)
        if v is not None:
            solves[r.get(args.key)] = v

    both = [k for k in verdicts if k in solves]
    print(f"audited {len(verdicts)}, solved-measured {len(solves)}, "
          f"both {len(both)}")

    buckets = collections.defaultdict(list)
    for k in both:
        buckets[verdicts[k]].append(solves[k])

    print(f"\n{'verdict':18s}{'n':>6s}{'mean pass@k':>14s}"
          f"{'solved 0 times':>16s}{'solved every time':>19s}")
    for verdict, vals in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        n = len(vals)
        print(f"{str(verdict):18s}{n:>6d}{sum(vals) / n:>14.2f}"
              f"{sum(1 for v in vals if v == 0) / n:>15.0%}"
              f"{sum(1 for v in vals if v == 1) / n:>19.0%}")

    # The same data read the other way, because it answers a different question.
    # Above: does a verdict predict the solve rate. Here: of the tasks nobody
    # solved, what was wrong with them — which is only meaningful next to the
    # base rate, since a verdict handed out to 44% of the corpus explains
    # nothing by turning up on 49% of the failures.
    base = collections.Counter(verdicts[k] for k in both)
    zero = collections.Counter(verdicts[k] for k in both if solves[k] == 0)
    n_zero = sum(zero.values())
    print(f"\nOf the {n_zero} tasks solved 0 times:")
    print(f"{'verdict':18s}{'n':>5s}{'share':>9s}{'corpus base':>14s}{'lift':>8s}")
    for verdict, n in zero.most_common():
        share = n / n_zero
        b = base[verdict] / len(both)
        print(f"{str(verdict):18s}{n:>5d}{share:>8.0%}{b:>14.0%}"
              f"{share / b:>8.1f}x")

    solvable = buckets.get("solvable", [])
    rejected = [v for k, vals in buckets.items() if k != "solvable" for v in vals]
    if solvable and rejected:
        print(f"\nWhat rejecting everything but 'solvable' would cost: "
              f"{len(rejected)} tasks dropped, of which "
              f"{sum(1 for v in rejected if v == 1)} were solved every time and "
              f"{sum(1 for v in rejected if 0 < v < 1)} landed in the usable band.")
        keep_band = sum(1 for v in solvable if 0 < v < 1)
        print(f"  kept: {len(solvable)} tasks, {keep_band} of them in the band "
              f"({keep_band / len(solvable):.0%}) against "
              f"{sum(1 for v in solvable + rejected if 0 < v < 1) / len(both):.0%} "
              f"across the whole corpus.")


if __name__ == "__main__":
    main()
