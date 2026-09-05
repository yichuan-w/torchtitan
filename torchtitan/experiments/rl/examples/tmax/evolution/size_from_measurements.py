#!/usr/bin/env python3
"""Give every row the sandbox its measurement says it needs.

The previous rule only wrote a resource key when the measurement exceeded the
fleet default, so a task measured at 300 MB and a task measured at 1.9 GB both
silently took the same 2 GiB. That throws away the number and provisions by
threshold instead of by evidence.

Rule here: measured peak x1.3, rounded up, floor 1 GiB, capped at the platform's
per-sandbox limits. A row whose measurement hit its ceiling is left alone: that
reading is the cap, not the requirement, so lowering it would be provisioning
from a truncated number.

Sources: TW disk from measure_disk.py (real block usage after a server-side
build); TMax ram/disk from Fzz1/Tmax-Tasks-Clean, whose peak_ram_mb is
env-inclusive, i.e. what must actually be provisioned. TW cpu/memory have never
been measured, only declared in each task.toml, so they are left as they are.

--apply writes through layout.write_mix: on a root's live mix that publishes the
next version, and the history is the backup.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout

HEADROOM = 1.3
MEM_CAP, DISK_CAP = 8, 10


def gib(mb: float | None, cap: int) -> int | None:
    if not isinstance(mb, (int, float)) or mb <= 0:
        return None
    return min(max(math.ceil(mb * HEADROOM / 1024), 1), cap)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix", default=None, help="default: $TRL_BASE/data/mix/live.jsonl")
    ap.add_argument("--tw-disk", default=None,
                    help="measure_disk.py output; default: $TRL_BASE/results/disk_full.jsonl")
    ap.add_argument("--tmax", default=None,
                    help="default: $TRL_BASE/data/sources/tmax-clean/splits/train.parquet")
    ap.add_argument("--holdout-n", type=int, default=64)
    ap.add_argument("--include-holdout", action="store_true",
                    help="also resize the held-out tail (changes what validation runs)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = layout.Root.from_env()
    mix = Path(args.mix) if args.mix else root.mix.live
    tw_disk = Path(args.tw_disk) if args.tw_disk else root.path / "results" / "disk_full.jsonl"
    tmax = (Path(args.tmax) if args.tmax
            else root.data / "sources" / "tmax-clean" / "splits" / "train.parquet")

    tw = {}
    for line in open(tw_disk):
        if line.strip():
            r = json.loads(line)
            if r.get("built"):
                tw[r["task_id"]] = r.get("disk_used_mb")

    import pyarrow.parquet as pq  # noqa: PLC0415
    tm = {r["task_id"]: r
          for r in pq.read_table(tmax).to_pylist()}

    lines = [l for l in mix.read_text().splitlines() if l.strip()]
    n = len(lines)
    editable = n if args.include_holdout else n - args.holdout_n

    out, stats = [], collections.Counter()
    for i, line in enumerate(lines):
        if i >= editable:
            out.append(line)
            stats["holdout untouched"] += 1
            continue
        row = json.loads(line)
        md = row["metadata"]
        tid = md.get("instance_id", "")
        want = {}
        if tid in tm:
            r = tm[tid]
            if r.get("ram_at_ceiling") or r.get("disk_at_ceiling"):
                stats["at ceiling, left alone"] += 1
            else:
                mem = gib(r.get("peak_ram_mb"), MEM_CAP)
                dsk = gib(r.get("peak_disk_mb"), DISK_CAP)
                if mem: want["daytona_mem_gb"] = mem
                if dsk: want["daytona_disk_gb"] = dsk
        elif tid in tw:
            dsk = gib(tw[tid], DISK_CAP)
            if dsk: want["daytona_disk_gb"] = dsk
        else:
            stats["no measurement"] += 1

        want = {k: v for k, v in want.items() if md.get(k) != v}
        if not want:
            out.append(line)
            stats["already correct"] += 1
            continue
        for k, v in want.items():
            stats[f"set {k}"] += 1
        md.update(want)
        out.append(json.dumps(row, ensure_ascii=("\\u" in line)))
        stats["changed"] += 1

    for k, v in stats.most_common():
        print(f"  {k}: {v}")
    if not args.apply:
        print(f"dry run -- {stats['changed']} rows would change")
        return
    published = layout.write_mix(mix, out)
    print(f"wrote {mix} ({stats['changed']} changed)"
          + (f"; published mix v{published[0]:04d}" if published else ""))


if __name__ == "__main__":
    main()
