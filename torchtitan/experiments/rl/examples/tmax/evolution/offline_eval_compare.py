#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Compare two evals of the same tasks trial by trial, per task.

Reads the eval trace recorder's index.json of an eval on the original rows and
of one on the hardened rows (offline_evalsets.py cuts the two sets), and
prints, per task, how many trials passed on each, then the histogram of the
change. Trials that failed on infrastructure (`infra_failed`) are counted
separately and excluded from the pass rates, since the trainer scores them
0.0 and they say nothing about the task.

    offline_eval_compare.py --original <eval>/trainer/validation_traces/step-0/index.json \
        --hardened <eval>/.../index.json [--out <json file>]
"""
from __future__ import annotations

import argparse
import collections
import json


def _per_task(path: str) -> dict[str, dict[str, int]]:
    rows = json.load(open(path))
    out: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"pass": 0, "fail": 0, "infra": 0}
    )
    for r in rows:
        bucket = (
            "infra"
            if r.get("infra_failed")
            else ("pass" if r["state"] == "PASS" else "fail")
        )
        out[r["task"]][bucket] += 1
    return dict(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--original", required=True)
    ap.add_argument("--hardened", required=True)
    ap.add_argument("--out", help="write the per-task table as JSON")
    a = ap.parse_args()
    o, h = _per_task(a.original), _per_task(a.hardened)
    if set(o) != set(h):
        raise SystemExit(f"task sets differ: {sorted(set(o) ^ set(h))[:5]}")
    table = []
    for t in sorted(o):
        og, hg = o[t]["pass"] + o[t]["fail"], h[t]["pass"] + h[t]["fail"]
        table.append(
            {
                "task": t,
                "original": f"{o[t]['pass']}/{og}",
                "hardened": f"{h[t]['pass']}/{hg}",
                "original_rate": o[t]["pass"] / og if og else None,
                "hardened_rate": h[t]["pass"] / hg if hg else None,
                "infra": {"original": o[t]["infra"], "hardened": h[t]["infra"]},
            }
        )
    n = len(table)
    tot = lambda d, k: sum(v[k] for v in d.values())  # noqa: E731
    print(f"tasks={n}")
    for name, d in (("original", o), ("hardened", h)):
        graded = tot(d, "pass") + tot(d, "fail")
        print(
            f"{name}: pass {tot(d, 'pass')}/{graded} graded ({tot(d, 'pass') / graded:.3f}), "
            f"infra {tot(d, 'infra')}; tasks solved at least once "
            f"{sum(1 for v in d.values() if v['pass'])}/{n}, "
            f"tasks solved every trial {sum(1 for v in d.values() if v['fail'] == 0 and v['pass'])}/{n}, "
            f"tasks never solved {sum(1 for v in d.values() if v['pass'] == 0)}/{n}"
        )
    hist = collections.Counter()
    for row in table:
        hr = row["hardened_rate"] if row["hardened_rate"] is not None else 0.0
        hist[min(int(hr * 4), 3) if hr < 1 else 4] += 1
    labels = {0: "<25%", 1: "25-49%", 2: "50-74%", 3: "75-99%", 4: "100%"}
    print(
        "hardened pass rate per task: "
        + ", ".join(f"{labels[k]}: {hist[k]}" for k in sorted(labels))
    )
    still_easy = [r["task"] for r in table if r["hardened_rate"] == 1.0]
    never = [r["task"] for r in table if r["hardened_rate"] == 0.0]
    print(
        f"still solved every trial after hardening ({len(still_easy)}): {' '.join(still_easy)}"
    )
    print(f"never solved after hardening ({len(never)}): {' '.join(never)}")
    if a.out:
        with open(a.out, "w") as f:
            json.dump(
                {
                    "tasks": table,
                    "hardened_rate_histogram": {labels[k]: hist[k] for k in labels},
                },
                f,
                indent=1,
            )
            f.write("\n")
        print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
