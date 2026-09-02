#!/usr/bin/env python3
"""Export the aggregated measurements as the CSV published on the HF dataset.

Publishes several columns rather than one conclusion, following
Fzz1/Tmax-Tasks-Clean: the raw peaks, the peaks net of the measuring tool's own
footprint, and the size those imply. A consumer that disagrees with the headroom
rule can recompute from the net figures instead of being stuck with ours.

The `provision_*` columns are read from derive_sizing.py rather than recomputed
here, because a published number that disagrees with the one the training mix
uses is worse than no number at all. `oracle_*` is the reference solution's own
peak, measured at the platform ceiling; it is empty for the TMax half, which
ships no reference solution to run.
"""
from __future__ import annotations

import argparse
import csv
import json
import math

CODEX_RAM, CODEX_DISK = 359.5, 357.0
CPU_CAP, MEM_CAP, DISK_CAP = 4, 8, 10

COLUMNS = ["task_id", "half", "attempts", "peak_ram_mb", "peak_ram_task_mb",
           "ram_env_mb", "peak_disk_mb", "peak_cpu_cores", "cpu_seconds",
           "ram_net_mb", "disk_net_mb", "oracle_ram_mb", "oracle_disk_mb",
           "oracle_cpu_seconds", "oracle_reward",
           "provision_cpu", "provision_mem_gb", "provision_disk_gb"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default="/scratch/al9080/terminal-rl/measure/aggregated_both.jsonl")
    ap.add_argument("--sizing", default="/scratch/al9080/terminal-rl/measure/sizing_v2.jsonl")
    ap.add_argument("--oracle", nargs="+",
                    default=["/scratch/al9080/terminal-rl/measure/oracle_at_max.jsonl"])
    ap.add_argument("--out", default="/scratch/al9080/terminal-rl/measure/measured_resources.csv")
    a = ap.parse_args()

    sizing = {}
    for line in open(a.sizing):
        if line.strip():
            r = json.loads(line)
            sizing[r["task_id"]] = r
    # Kept whatever the outcome. A run that did not reach reward=1 carries no
    # usable peak -- it may have stopped before the step that costs the most --
    # but the outcome itself is worth publishing: these tasks are recorded as
    # reward_verdict=pass in tasks.parquet and no longer reach it, which is a
    # fact about the corpus that belongs beside the old verdict rather than
    # silently replacing it.
    # One entry per task holding the highest reading any at-max run produced,
    # which is what derive_sizing sized from; publishing a single run's number
    # beside a size derived from several would not add up.
    oracle: dict = {}
    for path in a.oracle:
        for line in open(path):
            if not line.strip():
                continue
            r = json.loads(line)
            cur = oracle.setdefault(r["task_id"], {})
            for k in ("mem_peak_mb", "df_used_mb", "cpu_seconds"):
                v = r.get(k)
                if v is not None and v > (cur.get(k) or 0):
                    cur[k] = v
            if (r.get("reward") or 0) > (cur.get("reward") or 0):
                cur["reward"] = r["reward"]

    audit = {}
    for line in open(a.audit):
        if line.strip():
            rec = json.loads(line)
            audit[rec["task_id"]] = rec

    rows = []
    # The union, not the audit's keys alone. A task that could not boot during
    # the agent round is absent from the audit, so iterating it drops the task
    # from the published table even though the oracle measured it -- which is
    # how tw_627786 and tw_693888, both repaired after that round, stayed
    # missing. An oracle-only row carries empty agent columns and the sizing
    # derive_sizing.py gave it, which is the honest shape: the reading exists,
    # the other one does not.
    _EMPTY_AGENT = {"n": 0, "peak_ram_mb": 0.0, "peak_ram_task_mb": 0.0,
                    "ram_env_mb": 0.0, "peak_disk_mb": 0.0,
                    "peak_cpu_cores": 0.0, "cpu_seconds": 0.0}
    for tid in sorted(set(audit) | set(oracle)):
        r = audit.get(tid) or {"task_id": tid, **_EMPTY_AGENT}
        measured_by_agent = tid in audit
        net_ram = max(r["peak_ram_mb"] - CODEX_RAM, 0)
        net_disk = max(r["peak_disk_mb"] - CODEX_DISK, 0)
        sz = sizing.get(tid, {})
        orc = oracle.get(tid, {})
        rows.append({
            "task_id": tid,
            "half": "tmax" if tid.startswith("task_") else "tw",
            "attempts": r["n"] if measured_by_agent else 0,
            "peak_ram_mb": round(r["peak_ram_mb"], 1) if measured_by_agent else "",
            "peak_ram_task_mb": (round(r["peak_ram_task_mb"], 1)
                                 if measured_by_agent else ""),
            "ram_env_mb": round(r["ram_env_mb"], 1) if measured_by_agent else "",
            "peak_disk_mb": round(r["peak_disk_mb"], 1) if measured_by_agent else "",
            "peak_cpu_cores": (round(r["peak_cpu_cores"], 3)
                               if measured_by_agent else ""),
            "cpu_seconds": round(r["cpu_seconds"], 1) if measured_by_agent else "",
            "ram_net_mb": round(net_ram, 1) if measured_by_agent else "",
            "disk_net_mb": round(net_disk, 1) if measured_by_agent else "",
            "oracle_ram_mb": (round(orc["mem_peak_mb"], 1)
                              if orc.get("reward") == 1
                              and orc.get("mem_peak_mb") is not None else ""),
            "oracle_disk_mb": (round(orc["df_used_mb"], 1)
                               if orc.get("reward") == 1
                               and orc.get("df_used_mb") is not None else ""),
            "oracle_cpu_seconds": (round(orc["cpu_seconds"], 1)
                                   if orc.get("cpu_seconds") is not None else ""),
            "oracle_reward": ("" if not orc else orc.get("reward", 0)),
            "provision_cpu": sz.get("cpu", min(max(math.ceil(r["peak_cpu_cores"]), 1), CPU_CAP)),
            "provision_mem_gb": sz.get("mem_gb",
                                       min(max(math.ceil(net_ram * 1.3 / 1024), 1), MEM_CAP)),
            "provision_disk_gb": sz.get("disk_gb",
                                        min(max(math.ceil(net_disk * 1.3 / 1024), 1), DISK_CAP)),
        })
    rows.sort(key=lambda x: x["task_id"])
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    halves = {}
    for r in rows:
        halves[r["half"]] = halves.get(r["half"], 0) + 1
    print(f"wrote {a.out}: {len(rows)} rows {halves}")


if __name__ == "__main__":
    main()
