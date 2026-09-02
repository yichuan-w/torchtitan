#!/usr/bin/env python3
"""Measure per-task disk / cpu / memory the way Fzz1/Tmax-Tasks-Clean did.

The TMax half of the mix carries measured peaks; the TW half carries a
build-time disk figure and, for cpu and memory, nothing but each task.toml's
declaration. Sizing the two halves from different evidence is what this fixes:
same procedure, same counters, same aggregation rule.

Procedure, from that dataset's PROCESS.md:

  * N independent attempts per task, each in a fresh container, driven by a
    strong agent actually solving the task. Peaks are a by-product of the solve,
    not of a separate synthetic run.
  * Two memory figures, because one is a trap. `/sys/fs/cgroup/memory.peak`
    counts from container start and so includes the agent toolchain unpack:
    that is `peak_ram_mb`, and it is what must actually be provisioned.
    `memory.current` sampled during the agent phase gives the task-attributable
    `peak_ram_task_mb`. The cgroup is read-only here, so the counter cannot be
    reset and the two cannot be collapsed into one number.
  * A run that hits its limit reports the limit. Those are flagged
    `at_ceiling` and excluded from the aggregate, which is why this boots at the
    platform maximum rather than at the fleet default: measuring inside a 2 GiB
    box can only ever discover that a task wants 2 GiB.

Departures, deliberate:

  * cpu is collected too (`cpu.stat` usage_usec, differenced across samples).
    Free here, and it is the one dimension neither half has ever measured.
  * Disk is sampled, not read once at the end. Measured in this environment: a
    400MB file written then deleted moves `du` 176MB -> 595MB -> 176MB and `df`
    36KB -> 419MB -> 40KB, while `io.stat` wbytes rises and never falls, so it
    counts cumulative writes rather than occupancy and cannot give a peak. `du`
    costs 39ms here, cheap enough to sample every second. `du` includes the
    image and `df` does not; the quota has to hold the image, so `du` is what
    sizes the sandbox and `df` is kept as the written-layer split.

Known bias, to carry into whatever consumes this: the numbers describe the
measuring agent's behaviour, not the trained policy's, and N attempts under-read
the tail relative to the 16 rollouts a task sees in training.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import shlex
import time
from pathlib import Path

# daytona.py evaluates HARNESS_LABELS at import time, so the label has to reach
# the environment before that import rather than in main(). Set there, every
# sandbox this script booted carried the training fleet's label instead of its
# own, and the sweeper and account snapshot could not tell them apart.
if "--label" in sys.argv:
    os.environ["TT_DAYTONA_LABEL"] = sys.argv[sys.argv.index("--label") + 1]
else:
    os.environ.setdefault("TT_DAYTONA_LABEL", "resource_measure")

import solve_daytona as sd  # noqa: E402

BASE = sd.BASE
# Results and the codex cache go to the trainer host's local RAID, not GPFS.
# The shared fileset is filled by other tenants -- a 1.3T runs/ directory took
# this measurement down at attempt 412 with "Disk quota exceeded" on a 5MB
# append, and nothing about that failure is under our control.
LOCAL = Path(os.environ.get("MEASURE_LOCAL_BASE",
                            "/scratch/al9080/terminal-rl/measure"))
log = logging.getLogger("measure_resources")

# Rows whose metadata comes straight from the mix, keyed by instance_id. Filled
# by --mix; empty means every task must resolve to a pool directory.
_MIX_META: dict[str, dict] = {}


def _load_mix(path: str) -> None:
    for line in open(path):
        if line.strip():
            md = json.loads(line).get("metadata") or {}
            # An evolved row builds from a dockerfile and carries image="",
            # so requiring an image silently drops it.
            if md.get("instance_id") and (md.get("image") or md.get("dockerfile")):
                _MIX_META[md["instance_id"]] = md


def _codex_tarball() -> bytes:
    """The codex tarball, fetched once to local disk and reused.

    105 of 138 failures in the first run were containers with no curl, no wget
    and no python: nothing in the image could fetch it, so the download has to
    come from outside. Uploading unconditionally would push 99MB x 1998
    attempts, so this is the fallback for images that cannot self-serve.
    """
    import urllib.request
    cache = LOCAL / "codex-x86_64-unknown-linux-musl.tar.gz"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".part")
        urllib.request.urlretrieve(sd.CODEX_URL, tmp)
        tmp.rename(cache)
    return cache.read_bytes()

# Platform per-sandbox maximum. Measuring below this manufactures ceilings.
BOOT_CPU, BOOT_MEM_GB, BOOT_DISK_GB = 4, 8, 10

_SAMPLER = (
    "S=/tmp/_rs.log; : > $S; "
    "( while :; do "
    "printf '%s %s %s %s %s\\n' \"$(date +%s)\" "
    "\"$(cat /sys/fs/cgroup/memory.current 2>/dev/null)\" "
    "\"$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)\" "
    "\"$(du -sx --block-size=1 / 2>/dev/null | cut -f1)\" "
    "\"$(df -B1 --output=used / 2>/dev/null | tail -1)\"; "
    "sleep 1; done >> $S 2>/dev/null ) & echo $! > /tmp/_rs.pid"
)

_READOUT = (
    "kill $(cat /tmp/_rs.pid) 2>/dev/null; "
    "echo '===SAMPLES==='; tail -5000 /tmp/_rs.log 2>/dev/null; "
    "echo '===FINAL==='; "
    "echo peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null); "
    "echo max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null); "
    "echo cpu=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null); "
    "echo cpumax=$(cat /sys/fs/cgroup/cpu.max 2>/dev/null); "
    "echo wbytes=$(awk '{for(i=1;i<=NF;i++) if($i ~ /^wbytes=/){split($i,a,\"=\"); s+=a[2]}} END{print s+0}' "
    "/sys/fs/cgroup/io.stat 2>/dev/null); "
    "echo disk=$(du -sx --block-size=1 / 2>/dev/null | cut -f1)"
)

_BASELINE = (
    "echo wbytes=$(awk '{for(i=1;i<=NF;i++) if($i ~ /^wbytes=/){split($i,a,\"=\"); s+=a[2]}} END{print s+0}' "
    "/sys/fs/cgroup/io.stat 2>/dev/null); "
    "echo cur=$(cat /sys/fs/cgroup/memory.current 2>/dev/null); "
    "echo peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null); "
    "echo cpu=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null); "
    "echo disk=$(du -sx --block-size=1 / 2>/dev/null | cut -f1)"
)


def _kv(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in (text or "").splitlines():
        k, _, v = line.strip().partition("=")
        v = v.strip()
        if k and v.isdigit():
            out[k.strip()] = int(v)
    return out


def _parse_samples(text: str) -> list[tuple[int, ...]]:
    """(unix_s, memory.current, cpu usage_usec, du bytes, df used bytes)."""
    rows = []
    for line in text.splitlines():
        p = line.split()
        if len(p) == 5 and all(x.isdigit() for x in p):
            rows.append(tuple(int(x) for x in p))
    return rows


async def one_attempt(tid: str, idx: int, model: str, effort: str,
                      budget: int, sem: asyncio.Semaphore) -> dict:
    rec = {"task_id": tid, "attempt": idx, "model": model, "effort": effort,
           "boot": {"cpu": BOOT_CPU, "mem_gb": BOOT_MEM_GB, "disk_gb": BOOT_DISK_GB},
           "ts": int(time.time())}
    md = _MIX_META.get(tid)
    if md is None:
        # TW rows are packed from a pool directory; TMax rows have no package,
        # they carry a prebuilt image and live only in the mix.
        src = sd.resolve_src(tid)
        if src is None:
            return {**rec, "ok": False, "why": "no_pool_dir_and_not_in_mix"}
        try:
            md = sd.pack.to_row(str(src))["metadata"]
        except Exception as e:  # noqa: BLE001
            return {**rec, "ok": False, "why": f"pack:{type(e).__name__}"}
    workdir = md.get("workdir") or "/workspace"
    t0 = time.time()
    async with sem:
        try:
            async with sd.boot_agent_sandbox(
                md.get("image") or "", dockerfile=md.get("dockerfile") or None,
                build_context=md.get("build_context") or None,
                install_claude=False, cpu=BOOT_CPU, memory=BOOT_MEM_GB,
                disk_gb=BOOT_DISK_GB,
            ) as sandbox:
                sb = sd.dr._Root(sandbox)
                # Toolchain first: its unpack belongs to the environment
                # baseline, exactly as the reference dataset counted it.
                # Toolchain download+unpack, same binary and path solve_daytona
                # uses, so the baseline below covers the same environment.
                py_prog = ("import urllib.request; urllib.request.urlretrieve("
                           f"{sd.CODEX_URL!r}, '/tmp/cx.tgz')")
                py_dl = ("PYBIN=$(command -v python3 || command -v python); "
                         f'"$PYBIN" -c {shlex.quote(py_prog)}')
                install = (
                    "test -x /usr/local/bin/codex || { "
                    f"( command -v curl >/dev/null 2>&1 && curl -fsSL {sd.CODEX_URL} -o /tmp/cx.tgz ) || "
                    f"( command -v wget >/dev/null 2>&1 && wget -qO /tmp/cx.tgz {sd.CODEX_URL} ) || "
                    f"( {py_dl} ) || "
                    "{ echo NO_DOWNLOADER >&2; exit 90; }; "
                    "tar -xzf /tmp/cx.tgz -C /tmp && "
                    "mv /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex && "
                    "chmod +x /usr/local/bin/codex; }"
                )
                rc_i, out_i, err_i = await sb.exec(install, check=False, timeout=420)
                if rc_i != 0:
                    # No downloader in the image, or its CA bundle rejects the
                    # release host. Push the tarball in instead; only `tar` has
                    # to exist on the far side.
                    await sb.write_file("/tmp/cx.tgz", _codex_tarball())
                    rc_i, out_i, err_i = await sb.exec(
                        "tar -xzf /tmp/cx.tgz -C /tmp && "
                        "mv /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex && "
                        "chmod +x /usr/local/bin/codex",
                        check=False, timeout=300)
                    rec["codex_uploaded"] = True
                    if rc_i != 0:
                        return {**rec, "ok": False,
                                "secs": round(time.time() - t0, 1),
                                "why": f"codex_upload_rc={rc_i}: {(out_i + err_i)[-160:]}"}
                rc_b, out_b, _ = await sb.exec(_BASELINE, check=False, timeout=300)
                base = _kv(out_b)
                key = os.environ.get("OPENAI_API_KEY") or ""
                api = os.environ.get("SYNTH_API_BASE",
                                     "https://us.api.openai.com/v1")
                await sb.write_file("/tmp/codex_prompt.txt",
                                    md["problem_statement"])
                agent = (
                    "rm -rf /root/.cxhome && mkdir -p /root/.cxhome && "
                    f"CODEX_HOME=/root/.cxhome env OPENAI_API_KEY={shlex.quote(key)} "
                    "codex exec --dangerously-bypass-approvals-and-sandbox "
                    "--skip-git-repo-check "
                    "-c model_providers.oai.name=openai "
                    f"-c model_providers.oai.base_url={shlex.quote(api)} "
                    "-c model_providers.oai.env_key=OPENAI_API_KEY "
                    "-c model_provider=oai "
                    f"-c model_reasoning_effort={shlex.quote(effort)} "
                    # No -C: codex inherits the container's own directory, which is
        # where a plain exec and the model's tmux pane both land.
        f"-m {shlex.quote(model)} "
                    "- < /tmp/codex_prompt.txt"
                )
                # The agent gets its own deadline inside the exec, so that a
                # run which uses its whole budget still reaches the readout.
                # Sharing one timeout killed the command before the counters
                # were read: 192 of the first 1061 "successful" attempts came
                # back with every peak at zero and a negative cpu delta,
                # because only the baseline had been taken.
                rc, out, err = await sb.exec(
                    # A background job with a sleeping killer rather than
                    # `timeout`, so the agent deadline does not add a second
                    # dependency on GNU coreutils. (The harness's own exec
                    # wrapper already requires it -- see the image requirements
                    # in tmax/runbook/RUNBOOK.md -- and images that ship only
                    # busybox timeout fail there regardless of what this does.)
                    f"{_SAMPLER}; "
                    f"( sh -c {shlex.quote(agent)} & AP=$!; "
                    f"( sleep {budget}; kill -TERM $AP 2>/dev/null; "
                    f"sleep 10; kill -KILL $AP 2>/dev/null ) & KP=$!; "
                    f"wait $AP; RC=$?; kill $KP 2>/dev/null; exit $RC ) "
                    f"> /tmp/_agent.log 2>&1; "
                    f"echo AGENT_RC=$?; {_READOUT}",
                    check=False, timeout=budget + 300)
                blob = out or ""
                samples = _parse_samples(blob.split("===SAMPLES===")[-1]
                                         .split("===FINAL===")[0])
                fin = _kv(blob.split("===FINAL===")[-1])
                agent_rc = next((int(l.split("=")[1]) for l in blob.splitlines()
                                 if l.startswith("AGENT_RC=")
                                 and l.split("=")[1].isdigit()), None)
                cur_peak = max((s[1] for s in samples), default=0)
                cpu_used = (fin.get("cpu", 0) - base.get("cpu", 0))
                wall = max(time.time() - t0, 1)
                # Peak concurrency, not the run average: provisioning follows
                # the busiest second, and a run that is idle most of the time
                # averages to nearly nothing while still needing the cores.
                cpu_peak = 0.0
                for a_s, b_s in zip(samples, samples[1:]):
                    dt = b_s[0] - a_s[0]
                    if dt > 0 and b_s[2] >= a_s[2]:
                        cpu_peak = max(cpu_peak, (b_s[2] - a_s[2]) / 1e6 / dt)
                rec.update({
                    "ok": True, "agent_rc": agent_rc, "exec_rc": rc,
                    "secs": round(time.time() - t0, 1), "samples": len(samples),
                    "ram_env_mb": round(base.get("cur", 0) / 1048576, 1),
                    "ram_env_peak_mb": round(base.get("peak", 0) / 1048576, 1),
                    "peak_ram_mb": round(fin.get("peak", 0) / 1048576, 1),
                    "peak_ram_task_mb": round(cur_peak / 1048576, 1),
                    "ram_limit_mb": round(fin.get("max", 0) / 1048576, 1)
                                    if fin.get("max") else None,
                    "cpu_seconds": round(cpu_used / 1e6, 2),
                    "cpu_avg_cores": round(cpu_used / 1e6 / wall, 3),
                    "cpu_peak_cores": round(cpu_peak, 3),
                    "cpu_limit_cores": BOOT_CPU,
                    "disk_env_mb": round(base.get("disk", 0) / 1048576, 1),
                    "disk_used_mb": round(fin.get("disk", 0) / 1048576, 1),
                    # The number that sizes the sandbox: du includes the image,
                    # which the quota has to hold, and sampling catches a file
                    # written and deleted before the run ends. du costs 39ms.
                    "peak_disk_mb": round(max((s[3] for s in samples),
                                              default=fin.get("disk", 0))
                                          / 1048576, 1),
                    "peak_written_mb": round(max((s[4] for s in samples),
                                                 default=0) / 1048576, 1),
                    "io_wbytes_mb": round(fin.get("wbytes", 0) / 1048576, 1),
                    "io_wbytes_agent_mb": round(
                        max(fin.get("wbytes", 0) - base.get("wbytes", 0), 0)
                        / 1048576, 1),
                })
                # A record with no counters is not a measurement. Without this
                # the row still says ok=True and silently joins the aggregate.
                if fin.get("peak", 0) <= 0 or fin.get("disk", 0) <= 0:
                    return {**rec, "ok": False,
                            "secs": round(time.time() - t0, 1),
                            "agent_rc": agent_rc, "exec_rc": rc,
                            "samples": len(samples),
                            "why": "readout_missing (agent likely hit its "
                                   "deadline before the counters were read)"}
                lim = fin.get("max") or 0
                rec["ram_at_ceiling"] = bool(lim and fin.get("peak", 0) >= lim * 0.98)
                rec["cpu_at_ceiling"] = bool(cpu_peak >= BOOT_CPU * 0.95)
                rec["disk_at_ceiling"] = bool(
                    max((sp[3] for sp in samples), default=fin.get("disk", 0))
                    >= BOOT_DISK_GB * 1073741824 * 0.98)
                return rec
        except Exception as e:  # noqa: BLE001
            return {**rec, "ok": False, "secs": round(time.time() - t0, 1),
                    "why": f"{type(e).__name__}: {str(e)[:160]}"}


async def main_async(a: argparse.Namespace) -> None:
    out = Path(a.out)
    done: set[tuple[str, int]] = set()
    if out.exists() and not a.overwrite:
        for line in open(out):
            if line.strip():
                r = json.loads(line)
                done.add((r["task_id"], r["attempt"]))
    ids = [x.strip() for x in open(a.tasks).read().split() if x.strip()]
    if a.limit:
        ids = ids[: a.limit]
    jobs = [(t, i) for t in ids for i in range(a.attempts)
            if (t, i) not in done]
    log.info("%d tasks x %d attempts = %d total, %d already done, %d to run",
             len(ids), a.attempts, len(ids) * a.attempts, len(done), len(jobs))
    sem = asyncio.Semaphore(a.concurrency)
    lock = asyncio.Lock()
    n = [0]

    async def run(t: str, i: int) -> None:
        # The agent exec has a deadline; boot, the codex upload and the
        # readout do not. A sandbox that dies underneath one of those leaves
        # the attempt awaiting a call that never returns, which looks like slow
        # progress rather than a stall. One outer deadline covers them all.
        outer = a.budget + 1800
        try:
            r = await asyncio.wait_for(
                one_attempt(t, i, a.model, a.effort, a.budget, sem),
                timeout=outer)
        except asyncio.TimeoutError:
            r = {"task_id": t, "attempt": i, "ok": False,
                 "why": f"hung past {outer}s outside any exec deadline"}
        async with lock:
            with open(out, "a") as f:
                f.write(json.dumps(r) + "\n")
            n[0] += 1
            log.info("[%d/%d] %s a%d ok=%s ram=%s task_ram=%s disk=%s cpu=%ss %ss",
                     n[0], len(jobs), t, i, r.get("ok"), r.get("peak_ram_mb"),
                     r.get("peak_ram_task_mb"), r.get("disk_used_mb"),
                     r.get("cpu_seconds"), r.get("secs"))

    await asyncio.gather(*(run(t, i) for t, i in jobs))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", default="resource_measure",
                    help="owner label stamped on every sandbox; keep it distinct "
                         "from the training fleet's so the sweeper and the "
                         "account snapshot can tell the two apart")
    ap.add_argument("--tasks", default=str(BASE / "data/mix/train_ready_ids.txt"))
    ap.add_argument("--out", default=str(LOCAL / "resources.jsonl"))
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--model", default="gpt-5.6-luna")
    ap.add_argument("--effort", default="max")
    ap.add_argument("--budget", type=int, default=2400,
                    help="seconds for the agent exec")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--mix", default="", help="read metadata from this mix for "
                                              "tasks that have no pool directory")
    a = ap.parse_args()
    if a.mix:
        _load_mix(a.mix)
    LOCAL.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOCAL / "measure_resources.log"),
                  logging.StreamHandler()])
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
