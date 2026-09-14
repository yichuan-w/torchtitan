#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""What the control path can still see while a student fills the sandbox's memory.

compile-compcert (TB 2.1, step-40 supplement, sandbox 2014fb8f) ended as
`terminal_state_unavailable`: the harness's tmux liveness probe returned 124
after 60 s while the student's opam build sat at 100% memory and 100% CPU. The
Daytona daemon answered the metrics API the whole time and the probe's own
`timeout` fired, so what did not answer was the tmux server, which shares the
student's cgroup.

One fresh sandbox per variant, same shape as that task (2 CPU, 4 GiB, 10 GiB),
a workload in a tmux pane the way Terminus runs the student, and a monitor
outside that asks three separate questions every few seconds, each with its
own timeout so one stalled path does not hide the others:

  daemon   - the SDK metrics call the failure diagnostics already use
  terminal - the exact `tmux display-message` probe TerminalLifecycle runs
  evidence - memory.events / memory.pressure / memory.current read by a plain
             exec, i.e. what the harness can record without tmux

Workloads (--workload):
  hog        synthetic: WORKERS processes holding HOLD_PCT of the limit in
             anonymous memory, spinning on allocation and file reads
  compcert   what the model ran: opam from apt, a default switch, then
             `opam install coq.8.16.1 menhir`. opam sizes its jobs from the
             host's core count (`opam var jobs` = 47 in a 2-CPU sandbox), so
             Coq builds with 47 jobs against 2 CPUs and 4 GiB.

Variants:
  baseline        the sandbox as the harness leaves it
  protected       the daemon and its shells moved to /sys/fs/cgroup/control
                  (memory.min, cpu.weight 10000); the pane shell moved to
                  /sys/fs/cgroup/student; the tmux server stays in control.
                  The student's memory.max is unchanged.
  watchdog-proc   protected + a PSI watchdog in control that, after N
                  consecutive seconds of `some` memory stall, SIGKILLs the
                  largest-RSS process in the student cgroup
  watchdog-group  the same watchdog killing the whole student cgroup, with
                  memory.oom.group=1
  protected-group protected with memory.oom.group=1 and no watchdog

Measured 2026-09-14 (results in terminalworld-seeds/results/sandbox-pressure-20260914):
the synthetic hog never reproduced the stall (runs 1-5: the kernel OOM-killed
it, and the terminal probe stayed under 1.1 s); the compcert workload did
(run 6). Unsplit, the terminal probe went 0.5 s -> 17.7 s -> three consecutive
20 s timeouts once memory hit the limit, until the kernel killed opam itself;
split, it stayed under 4.2 s and the kernel OOM-killed one coqc, after which
opam reported the failed build to a live shell. The watchdog never fired
(`some` peaked at 43%), so the harness adopted the split alone
(harness/agents/terminus_terminal.py, TerminalLifecycle.isolate).

Usage (from della, training venv, daytona env sourced, PYTHONPATH=<checkout>):
  python probe_sandbox_pressure.py --out run --variants baseline,protected \
      --workload compcert --duration 1800 --interval 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("TT_DAYTONA_LABEL", "pressure_probe")

from torchtitan.experiments.rl.harness.agents.claude_code import (  # noqa: E402
    boot_agent_sandbox,
)

log = logging.getLogger("probe_pressure")

# The image of the attempt this reproduces, with the same tmux preinstall the
# TB 2.1 eval rows carry.
IMAGE = "registry-1.docker.io/alexgshaw/compile-compcert@sha256:f60236a4fe55bbb3c573210fe0fcd91551f4ee97c417453cd4985be074b35411"
DOCKERFILE = (
    f"FROM {IMAGE}\n"
    "RUN if ! command -v tmux >/dev/null 2>&1; then apt-get update && "
    "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux; fi\n"
    "RUN tmux -V\n"
)
CPU, MEM_GB, DISK_GB = 2, 4, 10

INSPECT = r"""
set +e
echo "== uname"; uname -a
echo "== id"; id; echo "nproc=$(nproc)"
echo "== tools"; for t in perl python3 tmux awk pgrep setsid; do printf '%s=' $t; command -v $t || echo none; done
echo "== pid1"; cat /proc/1/comm; tr '\0' ' ' < /proc/1/cmdline; echo
echo "== cgroup of pid1 / self"; cat /proc/1/cgroup; cat /proc/self/cgroup
echo "== cgroup fs"; stat -fc %T /sys/fs/cgroup; grep cgroup /proc/mounts
echo "== root entries"; ls /sys/fs/cgroup | tr '\n' ' '; echo
for f in cgroup.controllers cgroup.subtree_control cgroup.type cgroup.kill memory.max memory.high memory.min \
         memory.low memory.swap.max memory.oom.group memory.current memory.peak memory.events memory.pressure \
         cpu.max cpu.pressure pids.max; do
  printf '%s: ' $f; (cat /sys/fs/cgroup/$f 2>&1 | tr '\n' ' '); echo
done
echo "== root procs"; wc -l < /sys/fs/cgroup/cgroup.procs
echo "== psi"; cat /proc/pressure/memory /proc/pressure/cpu 2>&1
echo "== meminfo"; grep -E 'MemTotal|SwapTotal|MemAvailable' /proc/meminfo
echo "overcommit=$(cat /proc/sys/vm/overcommit_memory) swappiness=$(cat /proc/sys/vm/swappiness)"
echo "== procs"
(ps -eo pid,ppid,comm,args 2>/dev/null \
  || for p in /proc/[0-9]*; do echo "$(basename $p) $(cat $p/comm 2>/dev/null)"; done) | head -40
echo "== daemon"
for p in $(pgrep -f daemon 2>/dev/null) 1; do
  echo "pid=$p comm=$(cat /proc/$p/comm 2>/dev/null) oom_score_adj=$(cat /proc/$p/oom_score_adj 2>/dev/null)" \
    "cgroup=$(cat /proc/$p/cgroup 2>/dev/null | tr '\n' ' ')"
done
echo "== opam jobs"; grep -rs '^jobs' /root/.opam/config /root/.opam/*/.opam-switch/switch-config 2>/dev/null | head -3
OPAMROOT=/root/.opam opam var jobs 2>/dev/null || echo "opam var jobs: unavailable"
echo "== write test"; mkdir /sys/fs/cgroup/tt_probe_w 2>&1 && echo mkdir_ok && rmdir /sys/fs/cgroup/tt_probe_w
echo "== dmesg"; dmesg 2>&1 | tail -3
"""

# The daemon is PID 1 and every process it starts, the harness's exec shells
# included, already lives in /init.scope; the root cgroup is empty with all
# controllers delegated. init.scope's own control files refuse writes, so the
# control plane is moved into a sibling cgroup of our own with memory.min
# (memory_recursiveprot is on), and the student is set apart by starting its
# tmux server in another sibling. The student's own memory.max stays "max":
# its budget is still the sandbox's 4 GiB.
PROTECT = r"""
set -e
cd /sys/fs/cgroup
test -d init.scope
ls -ld init.scope init.scope/memory.min memory.min
mkdir -p control student
moved=0; failed=0
for p in $(cat init.scope/cgroup.procs); do
  if echo $p > control/cgroup.procs 2>/dev/null; then moved=$((moved+1)); else failed=$((failed+1)); fi
done
echo 256M > control/memory.min
echo 10000 > control/cpu.weight
echo "${TT_OOM_GROUP:-0}" > student/memory.oom.group
echo "moved=$moved failed=$failed init_scope_procs=$(wc -l < init.scope/cgroup.procs)" \
  "control_procs=$(wc -l < control/cgroup.procs) pid1_cgroup=$(cat /proc/1/cgroup) self_cgroup=$(cat /proc/self/cgroup)"
echo "control.memory.min=$(cat control/memory.min) control.cpu.weight=$(cat control/cpu.weight)" \
  "student.cpu.weight=$(cat student/cpu.weight) control.current=$(cat control/memory.current)" \
  "student.controllers=$(cat student/cgroup.controllers) student.oom.group=$(cat student/memory.oom.group)" \
  "student.max=$(cat student/memory.max)"
"""

WATCHDOG_SH = r"""#!/bin/sh
# A PSI watchdog for the student cgroup, run from the init cgroup. After NEED
# consecutive seconds with `some` memory stall (avg10) above THRESH percent it
# acts: MODE=proc kills the largest-RSS process in the student cgroup, the way
# the kernel OOM killer would have if reclaim had failed, so the shell and tmux
# survive and the model sees "Killed"; MODE=group kills the whole cgroup.
MODE=${1:-proc}; THRESH=${2:-80}; NEED=${3:-5}; n=0
while :; do
  some=$(awk '/^some/{split($2,a,"="); print a[2]}' /sys/fs/cgroup/student/memory.pressure 2>/dev/null)
  full=$(awk '/^full/{split($2,a,"="); print a[2]}' /sys/fs/cgroup/student/memory.pressure 2>/dev/null)
  echo "$(date +%s) some_avg10=$some full_avg10=$full n=$n" \
    "current=$(cat /sys/fs/cgroup/student/memory.current)" >> /var/tmp/tt_watchdog.log
  if awk -v v="$some" -v t="$THRESH" 'BEGIN{exit !(v+0>t+0)}'; then n=$((n+1)); else n=0; fi
  if [ "$n" -ge "$NEED" ]; then
    if [ "$MODE" = group ]; then
      echo "$(date +%s) KILL group some=$some full=$full" \
        "events=$(tr '\n' ' ' < /sys/fs/cgroup/student/memory.events)" >> /var/tmp/tt_watchdog.log
      echo 1 > /sys/fs/cgroup/student/cgroup.kill
    else
      pid=$(for p in $(cat /sys/fs/cgroup/student/cgroup.procs); do
              awk -v p="$p" '/^VmRSS/{print $2, p}' /proc/$p/status 2>/dev/null
            done | sort -n | tail -1 | cut -d' ' -f2)
      echo "$(date +%s) KILL proc pid=$pid comm=$(cat /proc/$pid/comm 2>/dev/null)" \
        "rss_kb=$(awk '/^VmRSS/{print $2}' /proc/$pid/status 2>/dev/null) some=$some full=$full" \
        "events=$(tr '\n' ' ' < /sys/fs/cgroup/student/memory.events)" >> /var/tmp/tt_watchdog.log
      kill -9 "$pid"
    fi
    n=0
  fi
  sleep 1
done
"""

# What 2014fb8f actually ran: `opam install` with jobs = nproc, and nproc in
# the sandbox reports the host's cores, not the 2-CPU quota. So: WORKERS
# processes that together hold HOLD_PCT of the limit in anonymous memory and
# then spin on allocation and file reads, plus exec churn. The control plane
# has to get its probe shell scheduled against that.
HOG_SH = r"""#!/bin/sh
HOLD_PCT=${1:-97}; WORKERS=${2:-64}
LIMIT=$(cat /sys/fs/cgroup/memory.max 2>/dev/null)
case "$LIMIT" in max|"") LIMIT=$(awk '/MemTotal/{print $2*1024}' /proc/meminfo);; esac
MB_EACH=$((LIMIT / 1024 / 1024 * HOLD_PCT / 100 / WORKERS))
echo "limit=$LIMIT hold_pct=$HOLD_PCT workers=$WORKERS mb_each=$MB_EACH nproc=$(nproc)"
mkdir -p /var/tmp/tt_files
i=0; while [ $i -lt 16 ]; do head -c 8388608 /dev/urandom > /var/tmp/tt_files/f$i; i=$((i+1)); done
( while :; do /bin/true; /usr/bin/env true; done ) &
( while :; do /bin/true; /usr/bin/env true; done ) &
i=0
while [ $i -lt "$WORKERS" ]; do
  perl -e 'my ($mb, $file) = @ARGV; my @a; push @a, "x" x (1024*1024) for 1..$mb;
    while (1) { my $t = "y" x (2*1024*1024); open my $h, "<", $file or next; local $/; my $d = <$h>; close $h; }' \
    "$MB_EACH" "/var/tmp/tt_files/f$((i % 16))" &
  i=$((i+1))
done
wait
echo "workers exited rc=$?"
"""

# The tmux server is the harness's, so it stays with the control plane; only
# the pane shell, whose children are the student's processes, moves.
START_HOG = r"""
set -e
tmux new-session -d -s probe -x 200 -y 50
srv=$(tmux display-message -p -t probe '#{pid}')
pane=$(tmux display-message -p -t probe '#{pane_pid}')
if [ -d /sys/fs/cgroup/student ]; then echo $pane > /sys/fs/cgroup/student/cgroup.procs; fi
tmux send-keys -t probe "sh /var/tmp/tt_hog.sh ${TT_HOLD_PCT:-97} ${TT_WORKERS:-64}; echo HOG_DONE rc=\$?" Enter
sleep 1
echo "tmux_server=$srv server_cgroup=$(cat /proc/$srv/cgroup | tr '\n' ' ')" \
  "pane=$pane pane_cgroup=$(cat /proc/$pane/cgroup | tr '\n' ' ')"
"""

# What the model did in 2014fb8f, minus its detours: opam from apt, a default
# switch, then Coq 8.16.1 and menhir. opam sizes its jobs from the host's
# core count (`opam var jobs` = 47 in a 2-CPU sandbox, measured 2026-09-14),
# so Coq builds with 47 parallel jobs against 2 CPUs and 4 GiB.
COMPCERT_SH = r"""#!/bin/sh
export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get install -y opam
opam init --disable-sandboxing -y
eval $(opam env)
echo "opam jobs=$(opam var jobs 2>/dev/null)"
# The model answered opam's depext prompt by hand; --confirm-level does it here.
opam install coq.8.16.1 menhir -y --confirm-level=unsafe-yes
echo "opam install rc=$?"
"""

TERMINAL_PROBE = (
    "tmux display-message -p -t probe "
    "'#{pid}|#{pane_pid}|#{pane_dead}|#{pane_dead_status}|#{pane_dead_signal}'"
)

EVIDENCE = r"""
c=/sys/fs/cgroup
echo "current=$(cat $c/memory.current 2>/dev/null) anon=$(awk '/^anon /{print $2}' $c/memory.stat 2>/dev/null)" \
  "file=$(awk '/^file /{print $2}' $c/memory.stat 2>/dev/null) events=$(tr '\n' ' ' < $c/memory.events 2>/dev/null)"
echo "psi=$(tr '\n' ' ' < $c/memory.pressure 2>/dev/null) cpu.psi=$(tr '\n' ' ' < $c/cpu.pressure 2>/dev/null)" \
  "nr_running=$(awk '{print $4}' /proc/loadavg)"
if [ -d $c/student ]; then
  echo "student.current=$(cat $c/student/memory.current) student.events=$(tr '\n' ' ' < $c/student/memory.events)" \
    "student.procs=$(wc -l < $c/student/cgroup.procs)"
  echo "student.psi=$(tr '\n' ' ' < $c/student/memory.pressure)"
  echo "control.current=$(cat $c/control/memory.current) control.procs=$(wc -l < $c/control/cgroup.procs)" \
    "control.psi=$(tr '\n' ' ' < $c/control/memory.pressure)"
fi
tail -n 2 /var/tmp/tt_watchdog.log 2>/dev/null || :
"""

TEARDOWN = r"""
set +e
c=/sys/fs/cgroup
echo "== pane"; tmux capture-pane -p -t probe 2>&1 | grep -v '^$' | tail -25
echo "== root"; echo "peak=$(cat $c/memory.peak 2>/dev/null) events=$(tr '\n' ' ' < $c/memory.events 2>/dev/null)"
if [ -d $c/student ]; then
  echo "== student"
  echo "peak=$(cat $c/student/memory.peak) events=$(tr '\n' ' ' < $c/student/memory.events)" \
    "procs=$(wc -l < $c/student/cgroup.procs)"
fi
echo "== watchdog"; tail -n 20 /var/tmp/tt_watchdog.log 2>/dev/null
echo "== dmesg"; dmesg 2>&1 | grep -iE 'oom|killed|memory' | tail -20
echo "== procs"; ps -eo pid,stat,rss,comm 2>/dev/null | sort -k3 -n -r | head -12
"""

CLEANUP = (
    "[ -d /sys/fs/cgroup/student ] && echo 1 > /sys/fs/cgroup/student/cgroup.kill 2>/dev/null; "
    "tmux kill-server 2>/dev/null; pkill -9 -f tt_hog 2>/dev/null; pkill -9 perl 2>/dev/null; "
    "pkill -9 -f 'while :' 2>/dev/null; pkill -9 -f opam 2>/dev/null; pkill -9 coqc 2>/dev/null; "
    "pkill -9 -f tt_watchdog 2>/dev/null; rm -rf /dev/shm/tt_hog /var/tmp/tt_files; echo cleaned"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


async def run_exec(sb, cmd: str, *, timeout: int, wait: float) -> dict:
    """One harness exec with an outer wall clock; a stalled call is recorded, not awaited."""
    t0 = time.monotonic()
    entry = {"at": now(), "timeout": timeout, "wait": wait}
    try:
        rc, out, err = await asyncio.wait_for(
            sb.exec(cmd, user="root", timeout=timeout), timeout=wait
        )
        entry.update(rc=rc, out=(out or "")[-4000:], err=(err or "")[-500:])
    except asyncio.TimeoutError:
        entry.update(rc=None, stalled=True)
    except Exception as exc:  # noqa: BLE001 - the probe records, it does not decide
        entry.update(rc=None, error=f"{type(exc).__name__}: {str(exc)[:300]}")
    entry["secs"] = round(time.monotonic() - t0, 2)
    return entry


async def daemon_probe(sb, *, wait: float = 8.0) -> dict:
    t0 = time.monotonic()
    entry = {"at": now()}
    try:
        m = await asyncio.wait_for(sb._sb.get_metrics_latest(), timeout=wait)
        entry.update(
            ok=True,
            mem_used=getattr(m, "mem_used", None),
            mem_total=getattr(m, "mem_total", None),
            mem_cache=getattr(m, "mem_cache", None),
            cpu_used_pct=getattr(m, "cpu_used_pct", None),
        )
    except asyncio.TimeoutError:
        entry.update(ok=False, stalled=True)
    except Exception as exc:  # noqa: BLE001
        entry.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")
    entry["secs"] = round(time.monotonic() - t0, 2)
    return entry


def parse_terminal(entry: dict) -> dict:
    if entry.get("rc") != 0:
        return {}
    fields = (entry.get("out") or "").strip().split("|")
    if len(fields) != 5:
        return {"malformed": entry.get("out")}
    pid, pane_pid, dead, status, signal = fields
    return {
        "pid": pid,
        "pane_pid": pane_pid,
        "dead": dead,
        "status": status,
        "signal": signal,
    }


async def probe_one(
    variant: str,
    index: int,
    out_dir: Path,
    duration: float,
    interval: float,
    hold_pct: int,
    workers: int,
    workload: str,
) -> dict:
    name = f"{variant}-{index}"
    record: dict = {
        "name": name,
        "variant": variant,
        "started_at": now(),
        "cpu": CPU,
        "mem_gb": MEM_GB,
        "disk_gb": DISK_GB,
        "hold_pct": hold_pct,
        "workers": workers,
        "workload": workload,
    }
    path = out_dir / f"{name}.json"
    timeline_path = out_dir / f"{name}.timeline.jsonl"

    def save() -> None:
        path.write_text(json.dumps(record, indent=1, default=str) + "\n")

    save()
    try:
        async with boot_agent_sandbox(
            IMAGE,
            dockerfile=DOCKERFILE,
            install_claude=False,
            cpu=CPU,
            memory=MEM_GB,
            disk_gb=DISK_GB,
        ) as sb:
            record["sandbox_id"] = sb.sandbox_id
            log.info("%s: sandbox %s up", name, sb.sandbox_id)
            record["inspect"] = await run_exec(sb, INSPECT, timeout=60, wait=90)
            save()
            if variant != "baseline":
                oom_group = "1" if variant.endswith("-group") else "0"
                record["protect"] = await run_exec(
                    sb, f"TT_OOM_GROUP={oom_group}\n" + PROTECT, timeout=30, wait=60
                )
                save()
                if record["protect"].get("rc") != 0:
                    record["outcome"] = "protect_failed"
                    return record
            if variant.startswith("watchdog"):
                mode = "group" if variant.endswith("-group") else "proc"
                await sb.write_file("/var/tmp/tt_watchdog.sh", WATCHDOG_SH)
                record["watchdog_start"] = await run_exec(
                    sb,
                    f"setsid nohup sh /var/tmp/tt_watchdog.sh {mode} 80 5 >/dev/null 2>&1 & sleep 1; "
                    "pgrep -f tt_watchdog | head -1 | xargs -I{} sh -c 'echo pid={} cgroup=$(cat /proc/{}/cgroup)'",
                    timeout=20,
                    wait=40,
                )
                save()
            await sb.write_file(
                "/var/tmp/tt_hog.sh", COMPCERT_SH if workload == "compcert" else HOG_SH
            )
            record["start_hog"] = await run_exec(
                sb,
                f"TT_HOLD_PCT={hold_pct}\nTT_WORKERS={workers}\n" + START_HOG,
                timeout=30,
                wait=60,
            )
            save()
            hog_started = time.monotonic()
            dead_ticks = 0
            with timeline_path.open("a") as tl:
                while time.monotonic() - hog_started < duration:
                    tick_at = round(time.monotonic() - hog_started, 1)
                    daemon, terminal, evidence = await asyncio.gather(
                        daemon_probe(sb),
                        run_exec(sb, TERMINAL_PROBE, timeout=10, wait=20),
                        run_exec(sb, EVIDENCE, timeout=10, wait=20),
                    )
                    parsed = parse_terminal(terminal)
                    tick = {
                        "t": tick_at,
                        "daemon": daemon,
                        "terminal": terminal,
                        "terminal_parsed": parsed,
                        "evidence": evidence,
                    }
                    tl.write(json.dumps(tick, default=str) + "\n")
                    tl.flush()
                    log.info(
                        "%s t=%5.1f daemon=%s(%.1fs mem=%s cpu=%s) terminal=%s(%.1fs %s) evidence=%s(%.1fs) %s",
                        name,
                        tick_at,
                        "ok"
                        if daemon.get("ok")
                        else ("STALL" if daemon.get("stalled") else "ERR"),
                        daemon["secs"],
                        daemon.get("mem_used"),
                        daemon.get("cpu_used_pct"),
                        "ok"
                        if terminal.get("rc") == 0
                        else (
                            "STALL"
                            if terminal.get("stalled")
                            else f"rc={terminal.get('rc')}"
                        ),
                        terminal["secs"],
                        f"dead={parsed.get('dead')} sig={parsed.get('signal')} st={parsed.get('status')}"
                        if parsed
                        else "",
                        "ok"
                        if evidence.get("rc") == 0
                        else (
                            "STALL"
                            if evidence.get("stalled")
                            else f"rc={evidence.get('rc')}"
                        ),
                        evidence["secs"],
                        (evidence.get("out") or "").replace("\n", " | ")[:300],
                    )
                    if parsed.get("dead") == "1":
                        dead_ticks += 1
                        if dead_ticks >= 2:
                            record["outcome"] = "pane_dead"
                            break
                    await asyncio.sleep(interval)
                else:
                    record["outcome"] = "duration_elapsed"
            record["hog_secs"] = round(time.monotonic() - hog_started, 1)
            record["teardown"] = await run_exec(sb, TEARDOWN, timeout=60, wait=120)
            save()
            record["cleanup"] = await run_exec(sb, CLEANUP, timeout=30, wait=60)
            record["after_cleanup"] = await run_exec(sb, EVIDENCE, timeout=10, wait=30)
            record["daemon_after"] = await daemon_probe(sb)
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        log.exception("%s failed", name)
    record["finished_at"] = now()
    save()
    return record


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--variants", default="baseline,protected,watchdog-proc,watchdog-group"
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=240.0,
        help="seconds to watch the hog before tearing down",
    )
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument(
        "--hold-pct",
        type=int,
        default=97,
        help="the hog holds this percent of memory.max in anonymous memory",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=64,
        help="hog processes; opam and make use nproc, which reports the host",
    )
    ap.add_argument(
        "--workload",
        choices=("hog", "compcert"),
        default="hog",
        help="synthetic hog, or the opam/Coq install the failed attempt ran",
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(args.out / "probe.log"),
        ],
    )
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    (args.out / "inputs.json").write_text(
        json.dumps(
            {
                "argv": sys.argv,
                "image": IMAGE,
                "dockerfile": DOCKERFILE,
                "cpu": CPU,
                "mem_gb": MEM_GB,
                "disk_gb": DISK_GB,
                "hog_sh": HOG_SH,
                "watchdog_sh": WATCHDOG_SH,
                "protect": PROTECT,
                "hold_pct": args.hold_pct,
                "workers": args.workers,
                "workload": args.workload,
                "compcert_sh": COMPCERT_SH,
                "started_at": now(),
            },
            indent=1,
        )
        + "\n"
    )
    tasks = [
        probe_one(
            v,
            i,
            args.out,
            args.duration,
            args.interval,
            args.hold_pct,
            args.workers,
            args.workload,
        )
        for i, v in enumerate(variants)
    ]
    results = await asyncio.gather(*tasks)
    summary = [
        {k: r.get(k) for k in ("name", "sandbox_id", "outcome", "hog_secs", "error")}
        for r in results
    ]
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    log.info("summary: %s", json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(main())
