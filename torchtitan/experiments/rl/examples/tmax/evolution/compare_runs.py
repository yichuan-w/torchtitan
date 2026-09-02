#!/usr/bin/env python3
"""Put two or more synthesis runs side by side, as percentages of attempts.

`review_synth.py` scores one run against targets. This answers the other
question — did the change between two runs move anything — and it has to be
percentages, because runs differ in size and the raw counts of a 700-attempt run
and a 180-attempt one are not comparable by eye.

Usage: compare_runs.py v19 v20 v21
       compare_runs.py --glob 'results/synth_{}_p*.jsonl' v19 v21
       compare_runs.py 'v19=baseline-v19/synth_v19_p*.jsonl' v20 v21

The third form is there because runs do not all live in one place: a run that has
been archived sits under its own directory while the current one is still in
`results/`, and requiring one template for all of them means the archived runs
just get left out of the comparison.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json

# Ordered for reading, not exhaustive: whatever else the runs produce is appended
# below. A fixed list silently drops the buckets nobody thought to name, and a
# comparison that only shows 86% of the attempts will be read as if it showed all
# of them — the missing tail is exactly where an unexplained shift hides.
KEYS = ["accepted", "blocked", "synth_failed", "synth_incomplete",
        "preflight_failed", "build_failed", "oracle_failed", "audit_rejected",
        "too_easy", "too_hard", "rejected_environment", "rejected_unsolvable",
        "retune_preflight_failed", "retune_build_failed", "retune_oracle_failed"]


def load(pattern: str) -> list[dict]:
    rows = []
    for path in sorted(glob.glob(pattern)):
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--glob", default="results/synth_{}_p*.jsonl")
    args = ap.parse_args()

    tables = {}
    for run in args.runs:
        name, _, override = run.partition("=")
        rows = load(override or args.glob.format(name))
        tables[name] = (len(rows), collections.Counter(r.get("status") for r in rows))
    args.runs = [r.partition("=")[0] for r in args.runs]

    seen = {k for _, c in tables.values() for k in c}
    present = ([k for k in KEYS if k in seen]
               + sorted(k for k in seen if k not in KEYS))
    width = max(len(str(k)) for k in present) + 2

    print(f"{'':{width}s}" + "".join(f"{r:>14s}" for r in args.runs))
    print(f"{'attempts':{width}s}" + "".join(f"{n:>14d}" for n, _ in tables.values()))
    print("-" * (width + 14 * len(args.runs)))
    for key in present:
        line = f"{str(key):{width}s}"
        for _, counter in tables.values():
            n = sum(counter.values()) or 1
            v = counter.get(key, 0)
            line += f"{v:>7d}{100 * v / n:>6.0f}%"
        print(line)
    print("-" * (width + 14 * len(args.runs)))
    print(f"{'total':{width}s}" + "".join(
        f"{sum(c.values()):>7d}{100:>6.0f}%" for _, c in tables.values()))


if __name__ == "__main__":
    main()
