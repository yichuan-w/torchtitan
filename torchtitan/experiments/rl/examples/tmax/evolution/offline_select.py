#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Pick the tasks whose FIRST training group in one run was all-solved.

An offline evolution pass takes a finished run and hardens only the tasks the
policy found trivially easy on first contact: for each task with rollouts, the
lowest group id is its first group; the task is selected when every graded
attempt in it scored 1.0 and the run wrote a harder signal for that group.
Holdout rows (the mix's last 64) never have rollouts and are never selected.

    offline_select.py --run <run dir> --mix <seed mix jsonl> --pkgs <tasks dir> \
        --out <dir>

Writes first_group_all.json (every task's first-group outcome) and
selected.json (the picks, keyed by task, with the group id offline_stage.py
needs) under --out, and checks that every mix row has a package under --pkgs
whose tests/test.sh matches the row, so r0 will be the row the policy saw.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re

HOLDOUT_N = 64


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--run", required=True, help="the run directory (runs/<name>)")
    ap.add_argument("--mix", required=True, help="the seed mix the run trained on")
    ap.add_argument(
        "--pkgs", required=True, help="tasks/ directory holding one package per row"
    )
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    rows = [json.loads(l) for l in open(a.mix) if l.strip()]
    order = [r["metadata"]["instance_id"] for r in rows]
    holdout = set(order[-HOLDOUT_N:])
    ok = bad = missing = 0
    for r in rows:
        tid = r["metadata"]["instance_id"]
        d = os.path.join(a.pkgs, tid)
        if not os.path.isdir(d):
            missing += 1
            continue
        test_sh = open(os.path.join(d, "tests", "test.sh")).read()
        if test_sh == r["metadata"]["tmax"]["test_sh"]:
            ok += 1
        else:
            bad += 1
    print(f"packages vs rows: match {ok} differ {bad} missing {missing}")
    if bad or missing:
        raise SystemExit(
            "the packages under --pkgs are not the rows the run trained on"
        )

    res: dict[str, dict] = {}
    root = os.path.join(a.run, "rollouts")
    for t in sorted(os.listdir(root)):
        groups: dict[int, list[dict]] = collections.defaultdict(list)
        for f in os.listdir(os.path.join(root, t)):
            m = re.match(r"g(\d+)-r(\d+)\.jsonl$", f)
            if not m:
                continue
            with open(os.path.join(root, t, f)) as fh:
                groups[int(m.group(1))].append(json.loads(fh.readline()))
        if not groups:
            continue
        g = min(groups)
        recs = groups[g]
        graded = [
            x
            for x in recs
            if x.get("reward") in (0.0, 1.0) and not x.get("infra_failed")
        ]
        solved = sum(1 for x in graded if x["reward"] == 1.0)
        res[t] = {
            "group": g,
            "solved": solved,
            "graded": len(graded),
            "rollouts": len(recs),
            "groups": len(groups),
            "signal": os.path.exists(os.path.join(a.run, "signals", f"{t}--g{g}.json")),
            "in_mix": t in order,
            "holdout": t in holdout,
        }
    full = {
        t: v for t, v in res.items() if v["graded"] > 0 and v["solved"] == v["graded"]
    }
    sel = {
        t: v
        for t, v in full.items()
        if v["signal"] and v["in_mix"] and not v["holdout"]
    }
    print(f"tasks with rollouts: {len(res)} of {len(order)} rows")
    print(
        f"first group all-solved: {len(full)}; with a harder signal and outside the holdout: {len(sel)}"
    )
    print(
        "selected solved/graded:",
        dict(collections.Counter(f"{v['solved']}/{v['graded']}" for v in sel.values())),
    )
    json.dump(res, open(os.path.join(a.out, "first_group_all.json"), "w"), indent=1)
    json.dump(sel, open(os.path.join(a.out, "selected.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
