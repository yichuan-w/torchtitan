#!/usr/bin/env python3
"""Watch the disk while the reference solution runs, and name what fills it.

At boot these sandboxes hold 36 KB against a 2 or 10 GiB quota, so nothing is
full when session_create fails -- the space goes at runtime. The tasks the logs
blame have a shape in common: they write without a bound. tw_532828 forks a
child that `execlp`s `yes`, which prints forever while the parent waits for it
to finish; tw_262649 enumerates every string a regex matches; tw_648569 drives a
MySQL instance over a sysbench table.

If that is what happens, more disk cannot fix it -- 10 GiB fills as surely as 2,
only later, which is exactly the pattern in the logs. This samples `df` and
`df -i` once a second while solve.sh runs, then lists the largest paths, so the
answer is a curve and a filename rather than an inference.
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
    os.environ.setdefault("TT_DAYTONA_LABEL", "growth_probe")

import daytona_revalidate as dr  # noqa: E402
import solve_daytona as sd  # noqa: E402
from torchtitan.experiments.rl.examples.tmax.grading import seed_workspace  # noqa: E402
from torchtitan.experiments.rl.harness.agents.claude_code import boot_agent_sandbox  # noqa: E402

LOCAL = Path(os.environ.get("MEASURE_LOCAL_BASE", "/scratch/al9080/terminal-rl/measure"))
log = logging.getLogger("probe_growth")

# One shell that runs solve.sh detached and samples underneath it, so the series
# covers the run rather than what is left after it.
SCRIPT = r"""
cd {workdir} 2>/dev/null || cd /
nohup bash -c '{cmd}' > /tmp/solve.out 2>&1 &
SOLVE=$!
for i in $(seq 1 {n}); do
  B=$(df -B1 / | tail -1 | awk '{{print $3}}')
  I=$(df -i / | tail -1 | awk '{{print $3}}')
  echo "T $i $B $I"
  mkdir -p /root/.daytona/sessions/probe_$i 2>/tmp/mk.err || echo "MKDIR_FAILED $i $(cat /tmp/mk.err)"
  kill -0 $SOLVE 2>/dev/null || echo "SOLVE_EXITED $i"
  sleep 1
done
echo '--- 最大的路径 ---'
du -x -m / 2>/dev/null | sort -rn | head -12
echo '--- solve 输出尾部 ---'
tail -c 300 /tmp/solve.out 2>/dev/null
echo '--- 最大的文件 ---'
find / -xdev -type f -size +20M -printf '%s %p\n' 2>/dev/null | sort -rn | head -8
"""


async def probe(tid: str, disk: int, secs: int, cmd: str) -> dict:
    rec = {"task_id": tid, "disk_gb": disk, "ts": int(time.time())}
    src = sd.resolve_src(tid)
    if src is None:
        return {**rec, "ok": False, "why": "no_pool_dir"}
    row = sd.pack.to_row(str(src))
    md = row["metadata"]
    workdir = md.get("workdir") or "/app"
    sol = Path(src) / "solution"
    try:
        async with boot_agent_sandbox(
            md.get("image") or "", dockerfile=md.get("dockerfile") or None,
            build_context=md.get("build_context") or None,
            install_claude=False, cpu=2, memory=4, disk_gb=disk,
        ) as sandbox:
            sb = dr._Root(sandbox)
            if md.get("entrypoint"):
                await dr._start_entrypoint(sb, md["entrypoint"], workdir=workdir)
            await seed_workspace(sb, md.get("tmax") or {})
            for f in sorted(sol.rglob("*")):
                if f.is_file():
                    await sb.write_file(f"/solution/{f.relative_to(sol)}",
                                        f.read_text(errors="replace"))
            _, out, err = await sb.exec(
                SCRIPT.format(workdir=workdir, n=secs, cmd=cmd),
                check=False, timeout=secs + 300)
            series = [l.split()[1:] for l in (out or "").splitlines() if l.startswith("T ")]
            mk = [l for l in (out or "").splitlines() if l.startswith("MKDIR_FAILED")]
            return {**rec, "ok": True, "samples": series, "mkdir_failures": mk[:6],
                    "mkdir_failed_n": len(mk),
                    "report": (out or "").split("--- 最大的路径 ---", 1)[-1][:2500],
                    "stderr": (err or "")[-300:]}
    except Exception as e:  # noqa: BLE001
        return {**rec, "ok": False, "why": f"{type(e).__name__}: {str(e)[:250]}"}


async def main_async(a: argparse.Namespace) -> None:
    out = Path(a.out)

    async def run(t: str) -> None:
        try:
            r = await asyncio.wait_for(probe(t, a.disk, a.seconds, a.command),
                                       timeout=a.seconds + 900)
        except asyncio.TimeoutError:
            r = {"task_id": t, "ok": False, "why": "hung"}
        with open(out, "a") as f:
            f.write(json.dumps(r) + "\n")
        s = r.get("samples") or []
        if s:
            first, last = s[0], s[-1]
            log.info("%s disk=%dGi 用量 %s -> %s bytes, inode %s -> %s (%d 个采样)",
                     t, a.disk, first[1], last[1], first[2], last[2], len(s))
        else:
            log.info("%s 无采样: %s", t, str(r.get("why"))[:80])

    await asyncio.gather(*(run(t) for t in a.tasks))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+")
    ap.add_argument("--disk", type=int, default=10)
    ap.add_argument("--seconds", type=int, default=90)
    # Default is the task's own reference solution. Point it at an unbounded
    # writer to reproduce what an agent does that the reference never does:
    # tw_532828's solve.sh only compiles the program, and the program is the
    # thing that floods -- its child execs `yes`.
    ap.add_argument("--command", default="bash /solution/solve.sh")
    ap.add_argument("--out", default=str(LOCAL / "growth_probe.jsonl"))
    ap.add_argument("--label", default="growth_probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(LOCAL / "growth_probe.log")])
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
