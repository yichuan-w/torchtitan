#!/usr/bin/env python3
"""Backfill per-task sandbox sizing into the live mix, from MEASURED data.

Fleet defaults dropped to 1 vCPU / 2GB / 2GB disk, so tasks that need more
must say so per-row or they OOM / run out of disk and read as "too hard".
Three metadata keys are consumed by the rollouter as-is: ``daytona_cpu``,
``daytona_mem_gb``, ``daytona_disk_gb``.

Sources, in trust order:
  disk  measured ``recommend_daytona_gb`` from measure_disk.py's full-corpus
        run (du of the merged overlay after a real server-side build), capped
        at Daytona's 10GB per-sandbox ceiling. Only written when > the fleet
        default (2).
  cpu / memory  the task's own task.toml ``[environment] cpus / memory_mb``
        declarations, only when they exceed the TerminalWorld TEMPLATE values
        (2 vCPU / 4096MB) -- 674 of 695 tasks carry those verbatim as
        boilerplate, so treating them as needs would undo the 1/2/2 fleet
        sizing corpus-wide. Runtime still caps memory at TT_DAYTONA_MAX_MEM_GB.

Same guarantees as backfill_agent_timeout.py: byte-identical reproduction of
unchanged rows (per-row ensure_ascii detection), the last --holdout-n rows are
never touched and verified byte-identical, dry-run by default. --apply writes
through layout.write_mix: on a root's live mix that publishes the next version,
and the history is the backup.

Scope (methodology red line): overrides are for SEED rows only. The overrides
that landed on evolved (retuned) rows in the 2026-08-29 pass were reverted --
evolved tasks are experiment data, never hand-tuned per task; their sizing
comes from the loop's fold adapter, which is where a sizing fix belongs.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("needs python3.11+ for tomllib")
import tomllib

from backfill_agent_timeout import _ascii_style, _POOLS
from torchtitan.experiments.rl.examples.tmax import layout

HOLDOUT_N = int(os.environ.get("TMAX_HOLDOUT_N", "64"))
DISK_CAP_GB = 10          # Daytona per-sandbox ceiling (BadRequest above it)
FLEET_DISK_GB = 2
# cpu/mem thresholds are BOILERPLATE filters, not fleet defaults: 674 of 695
# tasks declare memory_mb=4096 and cpus=2 verbatim -- the TerminalWorld template
# values, not measured needs. Writing those back would undo the 1/2/2 fleet
# sizing for the whole corpus. Only declarations ABOVE the template are treated
# as real; genuinely 4GB-needing tasks will surface in per-task failure
# monitoring and get targeted overrides.
BOILERPLATE_CPU, BOILERPLATE_MEM_GB = 2, 4


def declared_cpu_mem(root: layout.Root, task_id: str) -> tuple[int | None, int | None]:
    """(cpus, mem_gb) the task declares above fleet defaults, else Nones."""
    for pool in _POOLS:
        toml = root.data / "sources" / pool / "tasks" / task_id / "task.toml"
        if not toml.exists():
            continue
        try:
            with open(toml, "rb") as f:
                env = (tomllib.load(f).get("environment") or {})
        except Exception:  # noqa: BLE001
            continue
        cpu = env.get("cpus")
        mem_mb = env.get("memory_mb")
        cpu = int(cpu) if isinstance(cpu, (int, float)) and cpu > BOILERPLATE_CPU else None
        mem = (
            math.ceil(mem_mb / 1024)
            if isinstance(mem_mb, (int, float)) and mem_mb > BOILERPLATE_MEM_GB * 1024
            else None
        )
        return cpu, mem
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix", default=None, help="default: $TRL_BASE/data/mix/live.jsonl")
    ap.add_argument("--disk-results", default=None,
                    help="measure_disk.py output; default: $TRL_BASE/results/disk_full.jsonl")
    ap.add_argument("--holdout-n", type=int, default=HOLDOUT_N)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = layout.Root.from_env()
    mix = Path(args.mix) if args.mix else root.mix.live
    disk_results = (Path(args.disk_results) if args.disk_results
                    else root.path / "results" / "disk_full.jsonl")

    disk_gb: dict[str, int] = {}
    for line in open(disk_results):
        r = json.loads(line)
        if r.get("built") and (r.get("recommend_daytona_gb") or 0) > FLEET_DISK_GB:
            disk_gb[r["task_id"]] = min(int(r["recommend_daytona_gb"]), DISK_CAP_GB)

    lines = [l for l in mix.read_text().splitlines() if l.strip()]
    rotation, holdout = lines[: -args.holdout_n], lines[-args.holdout_n:]
    print(f"mix: {len(lines)} rows = {len(rotation)} rotation + {args.holdout_n} holdout; "
          f"{len(disk_gb)} measured disk overrides available")

    out: list[str] = []
    stats = collections.Counter()
    for line in rotation:
        row = json.loads(line)
        md = row["metadata"]
        tid = md["instance_id"]
        want: dict[str, int] = {}
        if tid in disk_gb and md.get("daytona_disk_gb") != disk_gb[tid]:
            want["daytona_disk_gb"] = disk_gb[tid]
        cpu, mem = declared_cpu_mem(root, tid)
        if cpu and md.get("daytona_cpu") != cpu:
            want["daytona_cpu"] = cpu
        if mem and md.get("daytona_mem_gb") != mem:
            want["daytona_mem_gb"] = mem
        if not want:
            stats["unchanged"] += 1
            out.append(line)
            continue
        ea = _ascii_style(row, line)
        if ea is None:
            stats["style_unrecognised"] += 1
            print(f"  {tid}: line style unrecognised -- left unchanged")
            out.append(line)
            continue
        md.update(want)
        out.append(json.dumps(row, ensure_ascii=ea))
        for k in want:
            stats[k] += 1
        stats["changed"] += 1

    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    assert holdout == lines[-args.holdout_n:]
    if not args.apply:
        print(f"dry run -- pass --apply to write ({stats['changed']} rows would change)")
        return
    published = layout.write_mix(mix, out + holdout)
    check = [l for l in mix.read_text().splitlines() if l.strip()]
    assert len(check) == len(lines), "row count moved"
    assert check[-args.holdout_n:] == holdout, "holdout no longer byte-identical"
    print(f"wrote {mix} ({stats['changed']} rows changed)"
          + (f"; published mix v{published[0]:04d}" if published else ""))


if __name__ == "__main__":
    main()
