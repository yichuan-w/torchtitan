#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Turn an EvolveBench arm's eval attempts into a run directory offline_stage.py can stage.

An eval writes each attempt to evals/<eval>/validation_rollouts/<task>/g<N>-r<i>.jsonl in the
same record format as a training rollout, but with no signals/ beside them, so the loop cannot
take them as a rewrite's traces. This writes <out>/{launch.json, signals/<task>--g<N>.json,
rollouts/<task>/g<N>-r<i>.jsonl (hardlinks), selected.json} for the given tasks: one signal per
task, from every scored attempt on the task's newest revision across the arm's evals. A task
whose attempts came out mixed gets no signal, since it needs no rewrite.

    eval_signal_run.py --arm <arm root> --out <run dir> --tasks <task> [<task> ...]
    offline_stage.py --run <run dir> --selected <run dir>/selected.json --root <root>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
import time


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--arm", required=True, help="arm root holding evals/")
    ap.add_argument("--out", required=True, help="run directory to create")
    ap.add_argument("--tasks", nargs="+", required=True)
    a = ap.parse_args()
    if os.path.exists(a.out):
        sys.exit(f"{a.out} exists; refusing to write over it")
    name = os.path.basename(a.out.rstrip("/"))
    os.makedirs(os.path.join(a.out, "signals"))
    os.makedirs(os.path.join(a.out, "rollouts"))
    launch = None
    selected = {}
    for tid in a.tasks:
        recs = []
        for path in sorted(glob.glob(os.path.join(a.arm, "evals", "*", "validation_rollouts", tid, "*.jsonl"))):
            with open(path) as f:
                head = json.loads(f.readline())
            if head.get("infra_failed"):
                continue
            recs.append((head, path))
        if not recs:
            print(f"{tid}: no scored eval attempts; skipped")
            continue
        rev = max(int(h.get("rev") or 0) for h, _ in recs)
        recs = [(h, p) for h, p in recs if int(h.get("rev") or 0) == rev]
        solved = sum(1 for h, _ in recs if (h.get("reward") or 0) >= 1)
        if 0 < solved < len(recs):
            print(f"{tid}: rev {rev} mixed ({solved}/{len(recs)}); no signal")
            continue
        group = recs[0][0]["group"]
        attempts = []
        for i, (_, src) in enumerate(recs):
            rel = f"rollouts/{tid}/g{group}-r{i}.jsonl"
            os.makedirs(os.path.dirname(os.path.join(a.out, rel)), exist_ok=True)
            os.link(src, os.path.join(a.out, rel))
            attempts.append(rel)
        if launch is None:
            # The eval's own launch.json sits in its base directory, the attempt dir's name minus "--attempt-…".
            base = re.sub(r"--attempt-.*$", "", os.path.dirname(os.path.dirname(os.path.dirname(recs[0][1]))))
            launch = os.path.join(base, "launch.json")
        sig = {"attempts": attempts, "created": time.strftime("%Y%m%d-%H%M%SZ", time.gmtime()),
               "direction": "easier" if solved == 0 else "harder", "group": group, "rev": rev,
               "run": name, "solved": solved, "task": tid, "total": len(recs)}
        with open(os.path.join(a.out, "signals", f"{tid}--g{group}.json"), "w") as f:
            json.dump(sig, f, indent=1, sort_keys=True)
            f.write("\n")
        selected[tid] = {"group": group}
        print(f"{tid}: rev {rev} {solved}/{len(recs)} -> {sig['direction']} signal g{group}")
    if launch is None:
        sys.exit("no task got a signal")
    shutil.copy2(launch, os.path.join(a.out, "launch.json"))
    with open(os.path.join(a.out, "selected.json"), "w") as f:
        json.dump(selected, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {len(selected)} signals to {a.out}")


if __name__ == "__main__":
    main()
