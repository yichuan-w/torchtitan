#!/usr/bin/env python3
"""What one accepted task costs, and where the spend goes when it isn't accepted.

Acceptance rate alone cannot say whether a change helped. A gate that rejects a
task early and a gate that rejects it after four rollouts produce the same
percentage and differ by most of the run's cost, so the number that moves when
an early gate is added is this one, not the rate.

Per run it prints tokens and wall-clock per accepted task, then the same split by
status — so a bucket that is cheap to fail and one that is expensive to fail stop
looking alike.

Usage: cost_per_accepted.py 'v19=baseline-v19/synth_v19_p*.jsonl' v20 v21
"""
from __future__ import annotations

import argparse
import collections
import glob
import json


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


def tokens(rec: dict) -> int:
    u = rec.get("usage") or {}
    return int(u.get("prompt_tokens", 0)) + int(u.get("completion_tokens", 0))


def seconds(rec: dict) -> float:
    if rec.get("t_end") and rec.get("t_start"):
        return max(0.0, rec["t_end"] - rec["t_start"])
    return 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--glob", default="results/synth_{}_p*.jsonl")
    args = ap.parse_args()

    for run in args.runs:
        name, _, override = run.partition("=")
        rows = load(override or args.glob.format(name))
        if not rows:
            print(f"{name}: no records"); continue
        acc = [r for r in rows if r.get("status") == "accepted"]
        tot_tok = sum(tokens(r) for r in rows)
        tot_sec = sum(seconds(r) for r in rows)
        n_acc = len(acc) or 1

        print(f"\n{name}: {len(rows)} attempts, {len(acc)} accepted "
              f"({100 * len(acc) / len(rows):.0f}%)")
        print(f"  per accepted task: {tot_tok / n_acc / 1000:.0f}k tokens, "
              f"{tot_sec / n_acc / 60:.0f} min of worker time")

        by = collections.defaultdict(lambda: [0, 0, 0.0])
        for r in rows:
            b = by[r.get("status")]
            b[0] += 1
            b[1] += tokens(r)
            b[2] += seconds(r)
        print(f"    {'status':26s}{'n':>5s}{'tok/each':>11s}{'sec/each':>10s}"
              f"{'% of run tokens':>17s}")
        for status, (n, tok, sec) in sorted(
                by.items(), key=lambda kv: -kv[1][1]):
            print(f"    {str(status):26s}{n:>5d}{tok / n / 1000:>10.0f}k"
                  f"{sec / n:>10.0f}{100 * tok / max(1, tot_tok):>16.0f}%")


if __name__ == "__main__":
    main()
