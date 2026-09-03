#!/usr/bin/env python3
"""Size each task from three sources at once, because one of them under-reads.

The first pass sized from the agent measurement alone and broke 16 of 663 tasks:
the agent picks its own route through a task, so an expensive step it skipped --
`conda create`, `blkar decode` -- never entered the peak, while the reference
solution runs that step every time. Three tasks landed on 1 GiB against a real
1.27 GB and were OOM-killed; four landed on 1 GiB of disk and could not even
create a Daytona session.

So the recommendation is the max of what three independent sources say:

  agent   peak while gpt-5.6-luna solved it, net of codex's own footprint
  oracle  peak while solution/solve.sh ran, measured at the platform ceiling so
          nothing is truncated, with no codex installed and nothing to subtract.
          It also gives cpu: a solve that burns C cpu-seconds cannot finish
          inside a B-second budget on fewer than C/B cores, which is a bound the
          agent's peak-core reading does not provide. tw_177860 timed out at
          900s on 1 core and finished in 360s on 4.
  peer    another group's independent measurement of the same task, where one
          exists. The TMax half has no runnable reference solution, so this is
          the only second source it can get; it moves 5 of 400 tasks and leaves
          the rest where the agent measurement put them.
  author  the task's own req_memory_mb / req_cpus -- off by default, see below

The declaration is not a floor by default, and that is the one judgement call
here. Across 663 tasks it takes four distinct values (2048 on 388 of them, 4096
on 115, 1024 on 82, absent on 67): it is a template field, not a statement about
this task. Using it as a floor raises mean memory per sandbox from 1.10 to 2.18
GiB, which halves how many sandboxes the account can hold at once, in exchange
for a number nobody measured. The measurements already cover every failure
observed: all sixteen tasks the old rule broke come out at 2 GiB or more without
it. Pass --decl-floor to turn it on if a policy agent turns out to need more
than either the reference solution or the measuring agent did.

Headroom multiplies the measurements only. A floor already states a requirement;
multiplying it would inflate a number nobody measured.

The disk floor is separate and empirical: four tasks whose whole occupancy is
around 600 MB still fail to create a session inside a 1 GiB sandbox, so the box
needs room beyond what `du` reports and no measurement-derived number reaches
it. Anything below DISK_FLOOR_GB is raised to it.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

CODEX_RAM, CODEX_DISK = 359.5, 357.0
CPU_CAP, MEM_CAP, DISK_CAP = 4, 8, 10
# The platform's per-sandbox ceiling as a box (daytona.io/docs/en/limits). A
# reading taken at this size cannot be truncated by the box.
CEILING = {"cpu": CPU_CAP, "mem_gb": MEM_CAP, "disk_gb": DISK_CAP}
HEADROOM = 1.3
DISK_FLOOR_GB = 2
# A task measured by only one of the two workloads keeps the fleet default as a
# memory floor. Writing it a smaller size lowers a row nothing can check: all of
# the downside, none of the upside. It applies in both directions. A task with
# no passing oracle reading rests on the agent measurement, which is the reading
# that under-read 16 tasks into failure; a task absent from the agent round
# rests on the reference solution, which runs a route the agent may not. The
# whole TMax half is in the first category, along with the TW tasks whose
# reference no longer passes; tasks repaired after the agent round are in the
# second.
UNVERIFIED_MEM_FLOOR_GB = 2
# The solve budget the cpu bound is computed against; keep it equal to the
# --timeout the oracle run and the training harness use, or the bound describes
# a deadline nobody enforces.
SOLVE_BUDGET_S = 900


def size_from_oracle(mem_peak_mb: float | None, df_used_mb: float | None,
                     cpu_seconds: float | None, *,
                     solve_budget_s: int = SOLVE_BUDGET_S) -> dict:
    """The oracle terms of main()'s rule, for one reference-solution run.

    main() sizes a seed from three sources at once; this is what the oracle
    source alone contributes, and it is what the evolution loop applies to a
    task the agent just rewrote: the reference solution ran in the agent's
    container, the container's counters say what it took, and the rewritten
    task is provisioned from that reading, never from the agent's guess. The
    rule has to stay the one main() uses, or a task sized in the loop and the
    same task sized in the campaign come out different for no reason: HEADROOM
    on the measurement, DISK_FLOOR_GB under the disk, cpu from cpu-seconds over
    the solve budget, every dimension capped at the platform.

    An oracle reading on its own is one source, so the single-source memory
    floor applies exactly as it does in main(): what the reference solution
    does is not what an agent's route does, in either direction.

    A reading taken in a box the solution outgrew is the box, not the task;
    callers check `oom_kill` / disk exhaustion / timeout before trusting it.
    """
    mem_gb = max(math.ceil((mem_peak_mb or 0) * HEADROOM / 1024), 1,
                 UNVERIFIED_MEM_FLOOR_GB)
    disk_gb = max(math.ceil((df_used_mb or 0) * HEADROOM / 1024), DISK_FLOOR_GB)
    cpu = max(math.ceil((cpu_seconds or 0) / solve_budget_s), 1)
    return {"cpu": min(cpu, CPU_CAP), "mem_gb": min(mem_gb, MEM_CAP),
            "disk_gb": min(disk_gb, DISK_CAP)}


def load(path: str, key: str = "task_id") -> dict:
    out = {}
    for line in open(path):
        if line.strip():
            r = json.loads(line)
            out[r[key]] = r
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True, help="aggregated.jsonl")
    # Repeatable: each at-max run is an independent sample of the same
    # workload, and one that scored zero can still hold the highest reading.
    ap.add_argument("--oracle", required=True, nargs="+", help="oracle at-max jsonl(s)")
    # The mix rows do not carry the author's declarations; tasks.parquet does,
    # and it is the artifact the dataset card publishes them from.
    ap.add_argument("--decl", required=True, help="tasks.parquet, for req_memory_mb/req_cpus")
    ap.add_argument("--out", required=True)
    # Fangzhou's train split. The dataset's top-level metadata/tasks.parquet is
    # the legacy cut: same id format, 641 rows, and zero of them are ours.
    ap.add_argument("--peer", default=None,
                    help="parquet with task_id + peak_ram_task_mb + peak_disk_mb/disk_env_mb")
    ap.add_argument("--decl-floor", action="store_true",
                    help="raise sizes to the task's own declaration (see module docstring)")
    a = ap.parse_args()

    agent = load(a.agent)
    oracle: dict = {}
    for path in a.oracle:
        for tid, r in load(path).items():
            cur = oracle.setdefault(tid, {})
            for k in ("mem_peak_mb", "df_used_mb", "cpu_seconds"):
                v = r.get(k)
                if v is not None and v > (cur.get(k) or 0):
                    cur[k] = v
            # reward is the best any run achieved: one success is enough to say
            # the reference solution still works at this size.
            if (r.get("reward") or 0) > (cur.get("reward") or 0):
                cur["reward"] = r["reward"]
    peer = {}
    if a.peer:
        import pandas as pd
        pf = pd.read_parquet(a.peer)
        for r in pf.itertuples():
            ram = float(getattr(r, "peak_ram_task_mb", 0) or 0)
            disk = float(getattr(r, "peak_disk_mb", 0) or 0) - float(
                getattr(r, "disk_env_mb", 0) or 0)
            peer[r.task_id] = (max(ram, 0), max(disk, 0))
    import pandas as pd
    df = pd.read_parquet(a.decl)
    cols = [c for c in ("req_memory_mb", "req_cpus") if c in df.columns]
    decl = {r.task_id: {c: getattr(r, c) for c in cols}
            for r in df[["task_id", *cols]].itertuples()}

    rows, stats = [], collections.Counter()
    # Union, not just the agent measurement's keys. A task that could not boot
    # during the agent round is absent from it, and iterating that file alone
    # drops such a task silently -- it then carries no daytona_* fields at all
    # and falls back to the fleet default, which is the outcome the whole
    # measurement exists to replace. tw_627786 and tw_693888 arrived this way:
    # both were excluded for needing more disk than a sandbox has, both were
    # repaired, and both now have an oracle reading and no agent one.
    for tid in sorted(set(agent) | set(oracle)):
        ag = agent.get(tid) or {"peak_ram_mb": 0.0, "peak_disk_mb": 0.0,
                                "peak_cpu_cores": 0.0}
        orc = oracle.get(tid) or {}
        md = decl.get(tid) or {}
        # A peak from a run that failed is still memory the task really used, so
        # it raises the size like any other reading; what it cannot do is
        # certify one, since the run may have stopped before the most expensive
        # step. Those two are separate, and conflating them cost tw_419317 an
        # OOM: its failing run peaked at 1,716 MB, that reading was discarded
        # for not reaching full marks, and the agent's 207 MB sized it at 1 GiB.
        # So the peak counts whenever it exists; only `used_oracle`, which
        # decides whether the unverified floor applies, asks about the reward.
        used_oracle = orc.get("reward") == 1
        # One source is one source whichever one is missing. The published floor
        # exists because a size resting on a single reading should not go below
        # the fleet default, and that holds for an oracle-only task as much as
        # for an agent-only one: what the reference solution does is not what
        # the agent's route does, in either direction.
        single_source = tid not in agent or not used_oracle
        ag_ram = max(ag["peak_ram_mb"] - CODEX_RAM, 0)
        ag_disk = max(ag["peak_disk_mb"] - CODEX_DISK, 0)
        or_ram = orc.get("mem_peak_mb") or 0
        or_disk = orc.get("df_used_mb") or 0
        pe_ram, pe_disk = peer.get(tid, (0, 0))
        or_cpu = (orc.get("cpu_seconds") or 0) / SOLVE_BUDGET_S
        dec_ram = md.get("req_memory_mb") or 0
        dec_cpu = md.get("req_cpus") or 0
        dec_ram = 0 if dec_ram != dec_ram else dec_ram  # NaN from parquet
        dec_cpu = 0 if dec_cpu != dec_cpu else dec_cpu

        mem_gb = max(math.ceil(max(ag_ram, or_ram, pe_ram) * HEADROOM / 1024), 1)
        if single_source:
            mem_gb = max(mem_gb, UNVERIFIED_MEM_FLOOR_GB)
        if a.decl_floor and dec_ram:
            mem_gb = max(mem_gb, math.ceil(dec_ram / 1024))
        disk_gb = max(math.ceil(max(ag_disk, or_disk, pe_disk) * HEADROOM / 1024),
                      DISK_FLOOR_GB)
        cpu = max(math.ceil(ag["peak_cpu_cores"]), math.ceil(or_cpu), 1)
        if a.decl_floor and dec_cpu:
            cpu = max(cpu, int(dec_cpu))
        mem_gb, disk_gb, cpu = (min(mem_gb, MEM_CAP), min(disk_gb, DISK_CAP),
                                min(cpu, CPU_CAP))

        if math.ceil(or_cpu) > math.ceil(ag["peak_cpu_cores"]):
            stats["cpu 由 oracle 的 cpu-seconds 决定"] += 1
        if a.decl_floor and dec_ram and math.ceil(dec_ram / 1024) >= mem_gb:
            stats["内存由声明决定"] += 1
        elif or_ram > max(ag_ram, pe_ram): stats["内存由 oracle 决定"] += 1
        elif pe_ram > ag_ram: stats["内存由第二方测量决定"] += 1
        else: stats["内存由 agent 决定"] += 1
        if not used_oracle:
            stats[f"无 oracle 读数, 内存下限抬到 {UNVERIFIED_MEM_FLOOR_GB} GiB"] += 1
        rows.append({"task_id": tid, "cpu": cpu, "mem_gb": mem_gb, "disk_gb": disk_gb,
                     "agent_ram_mb": round(ag_ram, 1), "oracle_ram_mb": round(or_ram, 1),
                     "decl_ram_mb": dec_ram, "peer_ram_mb": round(pe_ram, 1),
                     "peer_disk_mb": round(pe_disk, 1),
                     "agent_disk_mb": round(ag_disk, 1),
                     "oracle_disk_mb": round(or_disk, 1),
                     "agent_cpu": round(ag["peak_cpu_cores"], 2),
                     "oracle_cpu_seconds": orc.get("cpu_seconds"),
                     "oracle_cpu_min_cores": round(or_cpu, 2), "decl_cpu": dec_cpu})

    Path(a.out).write_text("".join(json.dumps(r) + "\n" for r in rows))
    for k, v in stats.most_common():
        print(f"  {k}: {v}")
    for name in ("cpu", "mem_gb", "disk_gb"):
        c = collections.Counter(r[name] for r in rows)
        print(f"  {name}: " + "  ".join(f"{k}->{c[k]}" for k in sorted(c)))
    print(f"  合计 {len(rows)} 题, 写入 {a.out}")


if __name__ == "__main__":
    main()
