#!/usr/bin/env python3
"""Decide whether a `Killed` reference solution ran out of memory or out of time.

Three tasks in provisioning_check.jsonl exit 137 with `Killed` printed by their
own shell. Two causes produce exactly that: the cgroup's OOM killer, which means
the recommended memory is too small, and the verifier's own 900s exec deadline,
which means nothing about sizing. The exit code cannot tell them apart and the
elapsed time does not either, since it covers boot and grading as well.

`memory.events` can: the kernel increments `oom_kill` once per kill and never
for a deadline. This boots at the same recommended size, gives solve.sh room to
finish, and reads that counter, so the answer is a number rather than an
inference. The same overrides answer a wider question. Every task that failed the
provisioning check is marked `reward_verdict = pass` in the dataset card, so it
passed oracle validation once already -- and that run passed no `memory=`, which
means it got the harness default of 4 GiB while the provisioning check gave some
of these 1 GiB. Re-running them at 4 with the timeout unchanged separates a
recommendation that is too small from a task that is simply flaky.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

if "--label" in sys.argv:
    os.environ["TT_DAYTONA_LABEL"] = sys.argv[sys.argv.index("--label") + 1]
else:
    os.environ.setdefault("TT_DAYTONA_LABEL", "oom_probe")

import daytona_revalidate as dr  # noqa: E402
import solve_daytona as sd  # noqa: E402
from torchtitan.experiments.rl.examples.tmax.grading import grade_tmax, seed_workspace  # noqa: E402
from torchtitan.experiments.rl.examples.tmax.integrity_baseline import capture_baseline  # noqa: E402
from torchtitan.experiments.rl.harness.agents.claude_code import boot_agent_sandbox  # noqa: E402
from verify_provisioning import recommend  # noqa: E402

LOCAL = Path(os.environ.get("MEASURE_LOCAL_BASE", "/scratch/al9080/terminal-rl/measure"))
log = logging.getLogger("probe_oom")

# memory.peak is the high-water mark, memory.max the ceiling the cgroup enforces,
# and oom_kill the count that settles the question. Reading all three together
# makes a near-miss visible instead of only a hit.
READ = ("cat /sys/fs/cgroup/memory.events 2>/dev/null | tr '\\n' ' '; echo '|'; "
        "cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.max 2>/dev/null | tr '\\n' ' '")


async def probe(tid: str, cpu: int, mem: int, disk: int, timeout: int,
                cmd: str | None = None) -> dict:
    rec = {"task_id": tid, "cpu": cpu, "mem_gb": mem, "disk_gb": disk,
           "ts": int(time.time()), "solve_timeout": timeout, "command": cmd}
    src = sd.resolve_src(tid)
    if src is None:
        return {**rec, "ok": False, "why": "no_pool_dir"}
    row = sd.pack.to_row(str(src))
    md = row["metadata"]
    tmax = md.get("tmax") or {}
    workdir = md.get("workdir") or "/workspace"
    sol = Path(src) / "solution"
    t0 = time.time()
    try:
        async with boot_agent_sandbox(
            md.get("image") or "", dockerfile=md.get("dockerfile") or None,
            build_context=md.get("build_context") or None,
            install_claude=False, cpu=cpu, memory=mem, disk_gb=disk,
        ) as sandbox:
            sb = dr._Root(sandbox)
            if md.get("entrypoint"):
                await dr._start_entrypoint(sb, md["entrypoint"], workdir=workdir)
            await seed_workspace(sb, tmax)
            for f in sorted(sol.rglob("*")):
                if f.is_file():
                    await sb.write_file(f"/solution/{f.relative_to(sol)}",
                                        f.read_text(errors="replace"))
            # --command replaces the reference solution, which is how you ask
            # what a task pays for doing nothing. A grader built only from
            # "this must not exist" assertions is already satisfied by the
            # image, so it hands out full marks for an empty rollout and the
            # task teaches the policy nothing.
            cmdline = cmd or "bash /solution/solve.sh"
            # INTEGRITY BASELINE: taken with solution/ in and nothing run yet, so
            # the grade below re-digests exactly what training would.
            baseline = await capture_baseline(sb, tmax, workdir=workdir, timeout=120)
            code, out, err = await sb.exec(f"cd {workdir} && {cmdline}",
                                           check=False, timeout=timeout)
            solve_secs = round(time.time() - t0, 1)
            # Read the counters before grading, which runs more processes and
            # could itself trip a kill and muddy the attribution.
            _, ev, _ = await sb.exec(READ, check=False, timeout=60)
            reward = await grade_tmax(sb, tmax, workdir=workdir, baseline_digests=baseline)
            events, _, peaks = (ev or "").partition("|")
            kv = dict(zip(events.split()[::2], events.split()[1::2]))
            nums = [int(x) for x in peaks.split() if x.isdigit()]
            return {**rec, "solve_exit": code, "reward": reward,
                    "solve_secs": solve_secs, "secs": round(time.time() - t0, 1),
                    "oom_kill": int(kv.get("oom_kill", -1)),
                    "oom": int(kv.get("oom", -1)),
                    "mem_high_mb": round(nums[0] / 1048576, 1) if nums else None,
                    "mem_max_mb": round(nums[1] / 1048576, 1) if len(nums) > 1 else None,
                    "tail": ((out or "") + (err or ""))[-400:]}
    except Exception as e:  # noqa: BLE001
        return {**rec, "ok": False, "secs": round(time.time() - t0, 1),
                "why": f"{type(e).__name__}: {str(e)[:200]}"}


async def main_async(a: argparse.Namespace) -> None:
    sizes = {}
    for line in open(a.audit):
        if line.strip():
            r = json.loads(line)
            sizes[r["task_id"]] = recommend(r)
    out = Path(a.out)
    async def run(t: str) -> None:
        c, m, d = sizes[t]
        c = a.cpu_override or c
        m = a.mem_override or m
        d = a.disk_override or d
        budget = a.timeout + 1200
        try:
            r = await asyncio.wait_for(probe(t, c, m, d, a.timeout, a.command),
                                       timeout=budget)
        except asyncio.TimeoutError:
            r = {"task_id": t, "ok": False, "why": f"hung past {budget}s"}
        with open(out, "a") as f:
            f.write(json.dumps(r) + "\n")
        log.info("%s %dc/%dGi/%dGi exit=%s reward=%s oom_kill=%s peak=%sMB/%sMB %ss",
                 t, c, m, d, r.get("solve_exit"), r.get("reward"), r.get("oom_kill"),
                 r.get("mem_high_mb"), r.get("mem_max_mb"), r.get("solve_secs"))
    await asyncio.gather(*(run(t) for t in a.tasks))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+")
    ap.add_argument("--audit", default=str(LOCAL / "aggregated.jsonl"))
    ap.add_argument("--out", default=str(LOCAL / "oom_probe.jsonl"))
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--cpu-override", type=int, default=None)
    ap.add_argument("--mem-override", type=int, default=None)
    ap.add_argument("--disk-override", type=int, default=None)
    ap.add_argument("--command", default=None,
                    help="run this instead of solution/solve.sh; use ':' for a null action")
    ap.add_argument("--label", default="oom_probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(LOCAL / "oom_probe.log")])
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
