#!/usr/bin/env python3
"""Boot each task at the size the audit recommends and run its oracle there.

Measuring a peak is not the same as proving the size works. The measurement ran
at the platform maximum with codex in the sandbox and no grading phase; a
recommendation derived from it has never actually been booted. Tasks have been
observed exhausting their disk at these sizes, which the peak numbers do not
explain, so this runs the real thing: allocate exactly what the audit says,
execute solve.sh, upload the tests, grade, and record what breaks.

Failure here is the useful output. `session_disk_exhausted` or `no space left`
means the recommendation is too small and the measurement missed something;
reward < 1 with no infra error means the task itself is at fault -- unless the
kernel killed it, which the first run could not tell from its own 900s deadline
because both surface as exit 137.

So the cgroup counters are read after solve.sh and before grading. `oom_kill`
settles that question with a number, and `memory.peak` makes this run a second,
independent measurement: the agent measurement watches whatever path the model
chose, while the reference solution is fixed and is the one thing every task
must be able to run. Sizing from the agent alone under-read three tasks into an
OOM, so both peaks are wanted, and reading them here costs one exec.

Applies to the TW half only. This runs an oracle, which needs
`solution/solve.sh` from the task's package directory; TMax tasks ship no
reference solution and have no package, so pointing this at them returns
`no_pool_dir` for every row rather than a result.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import shlex
import sys
import time
from pathlib import Path

# Same import-time trap as measure_resources: daytona.py freezes
# HARNESS_LABELS when it is imported, so setting the label in main() is too
# late and every sandbox goes out under the training fleet's name.
if "--label" in sys.argv:
    os.environ["TT_DAYTONA_LABEL"] = sys.argv[sys.argv.index("--label") + 1]
else:
    os.environ.setdefault("TT_DAYTONA_LABEL", "provision_check")

import daytona_revalidate as dr  # noqa: E402
import solve_daytona as sd
from torchtitan.experiments.rl.examples.tmax.grading import grade_tmax, seed_workspace
from torchtitan.experiments.rl.examples.tmax.integrity_baseline import capture_baseline
from torchtitan.experiments.rl.harness.agents.claude_code import boot_agent_sandbox

CODEX_RAM, CODEX_DISK = 359.5, 357.0
# oom_kill is what separates a kernel kill from a deadline; memory.peak next to
# memory.max shows a near miss rather than only a hit.
CGROUP_READ = ("cat /sys/fs/cgroup/memory.events 2>/dev/null | tr '\\n' ' '; echo '|'; "
               "cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.max "
               "2>/dev/null | tr '\\n' ' '; echo '|'; "
               # cpu.stat's usage_usec over the solve's wall time gives the mean
               # cores the reference solution drew. It is a mean, so it is a
               # floor on the parallelism, not the peak -- but the agent
               # measurement is the only cpu source otherwise, and two tasks
               # timed out at 900s on its number while finishing at 4 cores.
               "awk '/usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null")
# The largest [agent] timeout_sec any task in the corpus declares, measured
# across all 1353 packages: {600: 153, 900: 696, 1200: 11, 1800: 387, 2400: 3,
# 2700: 13, 3600: 73, 5400: 5, 7200: 2}, the two 7200s being tw_12007's pool.
# verify() raises each task's deadline to its own declaration, so the outer
# asyncio guard has to sit above every one of them or it kills the slow tasks
# the per-task deadline was added to protect.
_MAX_DECLARED_BUDGET_S = 7200

LOCAL = Path(os.environ.get("MEASURE_LOCAL_BASE",
                            "/scratch/al9080/terminal-rl/measure"))
log = logging.getLogger("verify_provisioning")


def recommend(r: dict) -> tuple[int, int, int]:
    net_ram = max(r["peak_ram_mb"] - CODEX_RAM, 0)
    net_disk = max(r["peak_disk_mb"] - CODEX_DISK, 0)
    return (min(max(math.ceil(r["peak_cpu_cores"]), 1), 4),
            min(max(math.ceil(net_ram * 1.3 / 1024), 1), 8),
            min(max(math.ceil(net_disk * 1.3 / 1024), 1), 10))


def declared_solve_budget(src: Path, floor: int) -> int:
    """The task's own agent timeout, floored at the run's --timeout.

    A fixed deadline for every task checks the deadline, not the task. Three of
    the seven failures in the 2026-09-02 re-verification were this: tw_17818
    declares 1800s and finished in 954, tw_418406 declares 3600 and finished in
    1078, and both were killed at the tool's 900s default and recorded as task
    failures. The floor stays because a task that declares nothing, or declares
    something implausibly short, should still get the run's budget.
    """
    toml = src / "task.toml"
    if not toml.exists():
        return floor
    section = ""
    for raw in toml.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
        elif section == "[agent]" and line.startswith("timeout_sec"):
            try:
                return max(floor, int(float(line.split("=", 1)[1].strip())))
            except ValueError:
                return floor
    return floor


async def verify(tid: str, cpu: int, mem: int, disk: int,
                 sem: asyncio.Semaphore, timeout: int,
                 cmd: str | None = None) -> dict:
    rec = {"task_id": tid, "cpu": cpu, "mem_gb": mem, "disk_gb": disk,
           "ts": int(time.time())}
    src = sd.resolve_src(tid)
    if src is None:
        return {**rec, "ok": False, "why": "no_pool_dir"}
    try:
        row = sd.pack.to_row(str(src))
    except Exception as e:  # noqa: BLE001
        return {**rec, "ok": False, "why": f"pack:{type(e).__name__}"}
    md = row["metadata"]
    tmax = md.get("tmax") or {}
    workdir = md.get("workdir") or "/workspace"
    sol = Path(src) / "solution"
    if not (sol / "solve.sh").exists():
        return {**rec, "ok": False, "why": "no_solution"}
    timeout = declared_solve_budget(Path(src), timeout)
    rec["solve_budget_s"] = timeout
    t0 = time.time()
    async with sem:
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
                # --command replaces the reference solution. Running the null
                # action across the corpus asks what each task pays for doing
                # nothing: a grader assembled only from "this must not exist"
                # assertions is already satisfied by its own image, so it hands
                # full marks to an empty rollout. tw_158378 is one -- `prm list`
                # alone scores 1.0 there -- and the sweep is how you find the
                # rest instead of guessing at how many.
                # INTEGRITY BASELINE: solution/ in, nothing run yet; the grade
                # below re-digests it, as training does.
                baseline = await capture_baseline(sb, tmax, workdir=workdir, timeout=120)
                code, out, err = await sb.exec(
                    cmd or "bash /solution/solve.sh",
                    check=False, timeout=timeout)
                solve_secs = round(time.time() - t0, 1)
                # Before grading, which starts processes of its own and would
                # fold their memory into a peak meant to describe the solution.
                _, ev, _ = await sb.exec(CGROUP_READ, check=False, timeout=60)
                parts = (ev or "").split("|")
                toks = parts[0].split()
                kv = dict(zip(toks[::2], toks[1::2]))
                nums = [int(x) for x in (parts[1] if len(parts) > 1 else "").split()
                        if x.isdigit()]
                usec = next((int(x) for x in (parts[2] if len(parts) > 2 else "").split()
                             if x.isdigit()), None)
                reward = await grade_tmax(sb, tmax, workdir=workdir, baseline_digests=baseline)
                # What the box had left when everything was done.
                _, dfout, _ = await sb.exec(
                    "df -B1 --output=size,used / | tail -1", check=False, timeout=60)
                parts = (dfout or "").split()
                size_mb = used_mb = None
                if len(parts) == 2 and all(p.isdigit() for p in parts):
                    size_mb = round(int(parts[0]) / 1048576, 1)
                    used_mb = round(int(parts[1]) / 1048576, 1)
                blob = ((out or "") + (err or ""))[-400:]
                return {**rec, "ok": reward >= 1.0, "reward": reward,
                        "solve_exit": code, "secs": round(time.time() - t0, 1),
                        "solve_secs": solve_secs,
                        "cpu_seconds": round(usec / 1e6, 1) if usec else None,
                        "cpu_mean_cores": (round(usec / 1e6 / solve_secs, 2)
                                           if usec and solve_secs > 0 else None),
                        "oom_kill": int(kv.get("oom_kill", -1)),
                        "mem_peak_mb": round(nums[0] / 1048576, 1) if nums else None,
                        "mem_max_mb": (round(nums[1] / 1048576, 1)
                                       if len(nums) > 1 else None),
                        "df_size_mb": size_mb, "df_used_mb": used_mb,
                        "disk_exhausted": "no space left" in blob.lower(),
                        "tail": blob}
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            return {**rec, "ok": False, "secs": round(time.time() - t0, 1),
                    "disk_exhausted": ("no space left" in msg.lower()
                                       or "disk_exhausted" in msg.lower()),
                    "why": msg}


async def main_async(a: argparse.Namespace) -> None:
    recs = {}
    if a.sizing:
        for line in open(a.sizing):
            if line.strip():
                r = json.loads(line)
                recs[r["task_id"]] = (r["cpu"], r["mem_gb"], r["disk_gb"])
    else:
        for line in open(a.audit):
            if line.strip():
                r = json.loads(line)
                recs[r["task_id"]] = recommend(r)
    out = Path(a.out)
    done = set()
    if out.exists() and not a.overwrite:
        for line in open(out):
            if line.strip():
                done.add(json.loads(line)["task_id"])
    todo = [(t, *((4, 8, 10) if a.at_max else v))
            for t, v in sorted(recs.items()) if t not in done]
    if a.limit:
        todo = todo[: a.limit]
    log.info("%d tasks in audit, %d already verified, %d to run",
             len(recs), len(done), len(todo))
    sem = asyncio.Semaphore(a.concurrency)
    lock = asyncio.Lock()
    n = [0]

    async def run(t: str, c: int, m: int, d: int) -> None:
        # Only the two execs inside verify() carry deadlines; boot, the file
        # uploads and grading do not, and a sandbox that dies underneath them
        # leaves the task awaiting a call that never returns. Four tasks hung
        # that way with their sandboxes already destroyed, which reads as "still
        # running" and stalls the whole run behind them. One outer deadline
        # covers every step, including the ones added later.
        budget = max(a.timeout, _MAX_DECLARED_BUDGET_S) + 900
        try:
            r = await asyncio.wait_for(verify(t, c, m, d, sem, a.timeout, a.command),
                                       timeout=budget)
        except asyncio.TimeoutError:
            r = {"task_id": t, "cpu": c, "mem_gb": m, "disk_gb": d,
                 "ok": False, "why": f"hung past {budget}s outside any exec "
                                     f"deadline (boot, upload or grading)"}
        async with lock:
            with open(out, "a") as f:
                f.write(json.dumps(r) + "\n")
            n[0] += 1
            log.info("[%d/%d] %s %d/%dGi/%dGi ok=%s reward=%s disk_out=%s used=%s/%sMB %s",
                     n[0], len(todo), t, c, m, d, r.get("ok"), r.get("reward"),
                     r.get("disk_exhausted"), r.get("df_used_mb"),
                     r.get("df_size_mb"), str(r.get("why", ""))[:60])

    await asyncio.gather(*(run(t, c, m, d) for t, c, m, d in todo))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit", default=str(LOCAL / "aggregated.jsonl"))
    # Prefer the sizing derive_sizing.py produced. recommend() below is the old
    # agent-only rule, kept because probe_oom_suspects imports it, but a check
    # that re-derives its own sizes stops checking what the mix actually uses.
    ap.add_argument("--sizing", default=None,
                    help="derive_sizing.py output; overrides --audit")
    ap.add_argument("--out", default=str(LOCAL / "provisioning_check.jsonl"))
    ap.add_argument("--concurrency", type=int, default=64)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--label", default="provision_check")
    ap.add_argument("--overwrite", action="store_true")
    # Running at the platform ceiling turns this from a check into an unbiased
    # measurement of the reference solution: nothing is truncated by a limit,
    # so memory.peak is what the oracle wants rather than what it was allowed.
    ap.add_argument("--command", default=None,
                    help="run this instead of solution/solve.sh; ':' is the null action")
    ap.add_argument("--at-max", action="store_true",
                    help="boot every task at 4/8/10 instead of the audited size")
    a = ap.parse_args()
    LOCAL.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOCAL / "verify_provisioning.log"),
                  logging.StreamHandler()])
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
