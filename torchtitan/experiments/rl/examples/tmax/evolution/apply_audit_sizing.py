#!/usr/bin/env python3
"""Write the audited sizes into the live mix.

The sizes are read, not derived. derive_sizing.py owns the rule -- the max of
the agent measurement, the oracle measurement and the author's declaration --
and an earlier version of this file computed a rule of its own from the agent
peaks alone, which under-provisioned 16 tasks into OOM kills and failed session
creation. Two scripts deriving the same number independently is how they drift,
so this one only applies what that one decided.

Rows with no entry keep what they have. The held-out tail is left alone by
default: resizing it changes what validation runs, which is a separate decision
from sizing the training rotation.

--apply writes through layout.write_mix: on a root's live mix that publishes the
next version, and the history is the backup.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout

CPU_CAP, MEM_CAP, DISK_CAP = 4, 8, 10


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizing", default="/scratch/al9080/terminal-rl/measure/sizing_v2.jsonl",
                    help="derive_sizing.py output; task_id, cpu, mem_gb, disk_gb")
    ap.add_argument("--mix", default=None, help="default: $TRL_BASE/data/mix/live.jsonl")
    ap.add_argument("--holdout-n", type=int, default=64)
    ap.add_argument("--include-holdout", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    mix = Path(a.mix) if a.mix else layout.Root.from_env().mix.live

    want = {}
    for line in open(a.sizing):
        if line.strip():
            r = json.loads(line)
            want[r["task_id"]] = (min(r["cpu"], CPU_CAP), min(r["mem_gb"], MEM_CAP),
                                  min(r["disk_gb"], DISK_CAP))

    lines = [l for l in mix.read_text().splitlines() if l.strip()]
    n = len(lines)
    editable = n if a.include_holdout else n - a.holdout_n
    out, stats = [], collections.Counter()
    deltas = collections.Counter()
    for i, line in enumerate(lines):
        if i >= editable:
            out.append(line); stats["holdout untouched"] += 1; continue
        row = json.loads(line); md = row["metadata"]
        tid = md.get("instance_id")
        if tid not in want:
            out.append(line); stats["no measurement"] += 1; continue
        # Methodology red line: evolved rows are experiment output and are never
        # hand-tuned per task. Their sizing belongs to the loop's fold adapter.
        # A TMax row that carries a dockerfile was produced by evolution; the
        # original 378 ship a prebuilt image.
        if tid.startswith("task_") and md.get("dockerfile"):
            out.append(line); stats["evolved, left to the loop"] += 1; continue
        cpu, mem, disk = want[tid]
        cur = (md.get("daytona_cpu"), md.get("daytona_mem_gb"), md.get("daytona_disk_gb"))
        if cur == (cpu, mem, disk):
            out.append(line); stats["already correct"] += 1; continue
        for name, old, new in (("cpu", cur[0] or 1, cpu), ("mem", cur[1] or 2, mem),
                               ("disk", cur[2] or 2, disk)):
            if new > old: deltas[f"{name} up"] += 1
            elif new < old: deltas[f"{name} down"] += 1
        md["daytona_cpu"], md["daytona_mem_gb"], md["daytona_disk_gb"] = cpu, mem, disk
        out.append(json.dumps(row, ensure_ascii=("\\u" in line)))
        stats["changed"] += 1

    for k, v in stats.most_common(): print(f"  {k}: {v}")
    for k, v in sorted(deltas.items()): print(f"  {k}: {v}")
    if not a.apply:
        print(f"dry run -- {stats['changed']} rows would change")
        return
    published = layout.write_mix(mix, out)
    print(f"wrote {mix} ({stats['changed']} changed)"
          + (f"; published mix v{published[0]:04d}" if published else ""))


if __name__ == "__main__":
    main()
