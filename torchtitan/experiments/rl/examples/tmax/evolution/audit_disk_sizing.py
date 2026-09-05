#!/usr/bin/env python3
"""Cross the measured per-task disk usage against what the live mix declares.

A task whose real post-build footprint exceeds the fleet default gets a sandbox
too small to build in, and the rollout then fails for a reason that looks like
the task being hard. That failure feeds the evolution loop as signal, so an
under-declared row does not just waste rollouts, it corrupts the measurement.

Reads measure_disk.py's output (task_id -> recommend_daytona_gb, which is real
block usage times 1.3) and the live mix, and reports which rows need a
daytona_disk_gb they do not carry.

Usage:  audit_disk_sizing.py [--measured PATH] [--mix PATH] [--fleet-default 2]
        audit_disk_sizing.py --list-gap        # just the ids needing a bump
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measured", default=None,
                    help="measure_disk.py output; default: $TRL_BASE/results/disk_full.jsonl")
    ap.add_argument("--mix", default=None, help="default: $TRL_BASE/data/mix/live.jsonl")
    ap.add_argument("--fleet-default", type=int, default=2,
                    help="TT_DAYTONA_DISK_GB in the live env")
    ap.add_argument("--cap", type=int, default=10, help="platform per-sandbox cap")
    ap.add_argument("--list-gap", action="store_true")
    args = ap.parse_args()
    root = layout.Root.from_env()
    measured_path = Path(args.measured) if args.measured else root.path / "results" / "disk_full.jsonl"
    mix = Path(args.mix) if args.mix else root.mix.live

    measured = {}
    for line in open(measured_path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("built"):
            measured[r["task_id"]] = r

    rows = {}
    for line in open(mix):
        line = line.strip()
        if not line:
            continue
        md = json.loads(line).get("metadata") or {}
        tid = md.get("instance_id")
        if tid:
            rows[tid] = md

    gap, covered, ok, over_cap = [], 0, 0, []
    for tid, md in rows.items():
        m = measured.get(tid)
        if not m:
            continue
        covered += 1
        need = m.get("recommend_daytona_gb") or 0
        if need <= args.fleet_default:
            continue
        if need > args.cap:
            over_cap.append((tid, need, m.get("disk_used_mb")))
            continue
        declared = md.get("daytona_disk_gb")
        if declared and declared >= need:
            ok += 1
        else:
            gap.append((tid, need, declared, m.get("disk_used_mb")))

    if args.list_gap:
        for tid, _, _, _ in sorted(gap):
            print(tid)
        return

    print(f"mix rows: {len(rows)}   measured tasks: {len(measured)}")
    print(f"mix rows with a measurement: {covered} "
          f"({covered * 100 // max(len(rows), 1)}%), "
          f"{len(rows) - covered} unmeasured")
    print()
    print(f"needing more than the {args.fleet_default} GiB fleet default: "
          f"{ok + len(gap)}")
    print(f"  already declared big enough : {ok}")
    print(f"  UNDER-DECLARED (the gap)    : {len(gap)}")
    if over_cap:
        print(f"  above the {args.cap} GiB platform cap, cannot run at any sizing: "
              f"{len(over_cap)}")
        for tid, need, used in sorted(over_cap):
            print(f"      {tid}  needs {need} GiB (used {used} MB)")
    print()
    if gap:
        hist = collections.Counter(need for _, need, _, _ in gap)
        print("gap by required size:")
        for need in sorted(hist):
            print(f"  {need} GiB: {hist[need]} tasks")
        print()
        print("worst 15:")
        for tid, need, declared, used in sorted(gap, key=lambda x: -x[1])[:15]:
            d = declared if declared else f"none (env {args.fleet_default})"
            print(f"  {tid:<48} needs {need:>2} GiB, declares {d}, used {used} MB")


if __name__ == "__main__":
    main()
