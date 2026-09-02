#!/usr/bin/env python3
"""Turn per-attempt measurements into one sandbox size per task.

Aggregation follows Fzz1/Tmax-Tasks-Clean: the true peak is the max over
UN-PRESSURED attempts. An attempt that hit its limit measured the limit, so it
is dropped rather than averaged in; a task with no un-pressured attempt left is
reported and gets no override.

Two biases live in the numbers and belong in whatever reads them. The measuring
agent is gpt-5.6-luna, not the trained 9B, so these are that agent's costs.
Three attempts under-read the tail against the 16 rollouts a task sees per
training step.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

HEADROOM = 1.3
CPU_CAP, MEM_CAP, DISK_CAP = 4, 8, 10


def gib(mb: float, cap: int) -> int:
    return min(max(math.ceil(mb * HEADROOM / 1024), 1), cap)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measurements", nargs="+",
                    default=["/scratch/al9080/terminal-rl/measure/resources.jsonl"],
                    help="one or more per-attempt files; both halves of the mix "
                         "are measured by the same tool, so they aggregate "
                         "together under the same rule")
    ap.add_argument("--mix",
                    default="/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/mix_live.jsonl")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    by_task: dict[str, list[dict]] = collections.defaultdict(list)
    for path in a.measurements:
        for line in open(path):
            if line.strip():
                r = json.loads(line)
                if r.get("ok") and (r.get("peak_ram_mb") or 0) > 0:
                    by_task[r["task_id"]].append(r)

    agg, dropped_all = {}, []
    for tid, rs in by_task.items():
        clean = [r for r in rs if not (r.get("ram_at_ceiling")
                                       or r.get("cpu_at_ceiling")
                                       or r.get("disk_at_ceiling"))]
        if not clean:
            dropped_all.append(tid)
            continue
        agg[tid] = {
            "n": len(clean), "n_dropped": len(rs) - len(clean),
            "peak_ram_mb": max(r.get("peak_ram_mb") or 0 for r in clean),
            "peak_ram_task_mb": max(r.get("peak_ram_task_mb") or 0 for r in clean),
            "ram_env_mb": min(r.get("ram_env_mb") or 0 for r in clean),
            "peak_disk_mb": max(r.get("peak_disk_mb") or 0 for r in clean),
            "peak_cpu_cores": max(r.get("cpu_peak_cores") or 0 for r in clean),
            "cpu_seconds": max(r.get("cpu_seconds") or 0 for r in clean),
            "secs": max(r.get("secs") or 0 for r in clean),
        }
        agg[tid]["want_cpu"] = min(max(math.ceil(agg[tid]["peak_cpu_cores"]), 1), CPU_CAP)
        agg[tid]["want_mem_gb"] = gib(agg[tid]["peak_ram_mb"], MEM_CAP)
        agg[tid]["want_disk_gb"] = gib(agg[tid]["peak_disk_mb"], DISK_CAP)

    print(f"tasks measured: {len(by_task)}, aggregated: {len(agg)}"
          + (f", no un-pressured attempt: {len(dropped_all)}" if dropped_all else ""))

    def dist(key: str, unit: str) -> None:
        v = sorted(x[key] for x in agg.values())
        if v:
            print(f"  {key:<18} min {v[0]:.0f}  median {v[len(v)//2]:.0f}  "
                  f"p95 {v[int(len(v)*0.95)]:.0f}  max {v[-1]:.0f} {unit}")
    print("measured peaks:")
    for k, u in (("peak_ram_mb", "MB"), ("peak_ram_task_mb", "MB"),
                 ("ram_env_mb", "MB"), ("peak_disk_mb", "MB"),
                 ("peak_cpu_cores", "cores")):
        dist(k, u)

    want = collections.Counter((x["want_cpu"], x["want_mem_gb"], x["want_disk_gb"])
                               for x in agg.values())
    print("\nwould provision (cpu, mem GiB, disk GiB):")
    for k, n in want.most_common(10):
        print(f"  {k}: {n} tasks")

    cur = {}
    for line in open(a.mix):
        if line.strip():
            md = json.loads(line).get("metadata") or {}
            if md.get("instance_id"):
                cur[md["instance_id"]] = md
    tot_now = [0, 0, 0]
    tot_new = [0, 0, 0]
    changed = collections.Counter()
    for tid, m in agg.items():
        md = cur.get(tid)
        if md is None:
            continue
        now = (md.get("daytona_cpu") or 1, md.get("daytona_mem_gb") or 2,
               md.get("daytona_disk_gb") or 2)
        new = (m["want_cpu"], m["want_mem_gb"], m["want_disk_gb"])
        for i in range(3):
            tot_now[i] += now[i]
            tot_new[i] += new[i]
        for i, name in enumerate(("cpu", "mem", "disk")):
            if new[i] > now[i]:
                changed[f"{name} up"] += 1
            elif new[i] < now[i]:
                changed[f"{name} down"] += 1
    print(f"\nover the {sum(1 for t in agg if t in cur)} measured rows present in the mix:")
    print(f"  now      {tot_now[0]} vCPU / {tot_now[1]} GiB / {tot_now[2]} GiB")
    print(f"  measured {tot_new[0]} vCPU / {tot_new[1]} GiB / {tot_new[2]} GiB")
    for k, n in sorted(changed.items()):
        print(f"  {k}: {n}")

    if a.out:
        Path(a.out).write_text("".join(
            json.dumps({"task_id": t, **v}) + "\n" for t, v in sorted(agg.items())))
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
