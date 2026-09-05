#!/usr/bin/env python3
"""Emit the sizing verify_provisioning.py checks, taken from the mix rows.

Re-deriving the sizes here would check a rule, not a corpus: the thing that has
to boot is the size the row actually carries, so that is what is read out. Rows
with no daytona_* fields are reported and skipped rather than defaulted, because
a default here would silently verify a size the row does not have.

  sizing_from_mix.py --ids train_ready_ids.txt --out sizing_published.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix", default=None, help="default: $TRL_BASE/data/mix/live.jsonl")
    ap.add_argument("--ids", default=None,
                    help="restrict to these task ids, one per line; default: the TW "
                         "dataset's metadata/train_ready_ids.txt under data/sources/tw-extract")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = layout.Root.from_env()
    mix = Path(a.mix) if a.mix else root.mix.live
    ids = Path(a.ids) if a.ids else (root.data / "sources" / "tw-extract" / "metadata"
                                     / "train_ready_ids.txt")

    wanted = {x.strip() for x in ids.read_text().splitlines() if x.strip()}

    rows, unsized, missing = [], [], set(wanted)
    with open(mix) as fh:
        for line in fh:
            if not line.strip():
                continue
            m = json.loads(line)["metadata"]
            tid = m["instance_id"]
            if tid not in wanted:
                continue
            missing.discard(tid)
            cpu, mem, disk = (m.get("daytona_cpu"), m.get("daytona_mem_gb"),
                              m.get("daytona_disk_gb"))
            if None in (cpu, mem, disk):
                unsized.append(tid)
                continue
            rows.append({"task_id": tid, "cpu": cpu, "mem_gb": mem,
                         "disk_gb": disk})

    with open(a.out, "w") as fh:
        for r in sorted(rows, key=lambda r: r["task_id"]):
            fh.write(json.dumps(r) + "\n")

    print(f"wrote {len(rows)} sizings to {a.out}")
    if unsized:
        print(f"  {len(unsized)} rows carry no daytona_* sizing: {unsized[:8]}")
    if missing:
        print(f"  {len(missing)} requested ids are not in the mix: "
              f"{sorted(missing)[:8]}")


if __name__ == "__main__":
    main()
