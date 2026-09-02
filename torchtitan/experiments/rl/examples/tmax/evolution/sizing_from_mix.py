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
import os
from pathlib import Path

ROOT = Path(os.environ.get("TRL_ROOT", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix", default=str(ROOT / "data/mix/mix_live.jsonl"))
    ap.add_argument("--ids", default=str(ROOT / "data/mix/train_ready_ids.txt"),
                    help="restrict to these task ids, one per line")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    wanted = None
    if a.ids:
        wanted = {x.strip() for x in Path(a.ids).read_text().splitlines() if x.strip()}

    rows, unsized, missing = [], [], set(wanted or ())
    with open(a.mix) as fh:
        for line in fh:
            if not line.strip():
                continue
            m = json.loads(line)["metadata"]
            tid = m["instance_id"]
            if wanted is not None and tid not in wanted:
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
