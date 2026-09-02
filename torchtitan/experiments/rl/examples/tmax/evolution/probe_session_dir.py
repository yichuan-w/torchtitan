#!/usr/bin/env python3
"""Find out whose /root the failing mkdir is in.

The error is `Failed to create session: internal server error: failed to create
session config directory: mkdir /root/.daytona/sessions/<uuid>: no space left on
device`, and "internal server error" means the Daytona server said it, not the
container. So the path may not be the sandbox's /root at all -- it could be the
runner daemon's own state directory, in which case the sandbox's disk_gb is
irrelevant to it and every confusing observation follows without the platform
leaking one tenant's disk into another's.

The measurement that prompted this does not decide it. `df -i` reporting the
filesystem's inode counts while `df -B1` reports the quota is just what an XFS
project quota looks like when it limits blocks and not inodes; it says nothing
about where the daemon writes.

So: boot a sandbox, and look for /root/.daytona from inside. If the session
directories are there, the mkdir is in the container and its quota governs. If
they are not, the path belongs to the runner and no amount of disk_gb touches it.
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
    os.environ.setdefault("TT_DAYTONA_LABEL", "sessdir_probe")

import daytona_revalidate as dr  # noqa: E402
import solve_daytona as sd  # noqa: E402
from torchtitan.experiments.rl.harness.agents.claude_code import boot_agent_sandbox  # noqa: E402

LOCAL = Path(os.environ.get("MEASURE_LOCAL_BASE", "/scratch/al9080/terminal-rl/measure"))
log = logging.getLogger("probe_inodes")

# Blocks and inodes side by side, plus what the mount actually is. Read straight
# after boot, before anything of ours runs, because session_create fails there.
# `df -i` refuses --output on this coreutils, so read it plain. The pairing is
# the point: if the block quota is private and the inode pool is not, then size
# tracks disk_gb while the inode totals and used counts match the host and drift
# between sandboxes booted at the same moment.
READ = (
    "echo '## /root/.daytona 是否存在于容器内'; ls -la /root/.daytona/ 2>&1 | head -6; echo; "
    "echo '## sessions 目录内容'; ls -la /root/.daytona/sessions/ 2>&1 | head -8; echo; "
    "echo '## 它和 / 是不是同一个文件系统'; stat -c '%d %n' / /root /root/.daytona 2>&1; echo; "
    "echo '## /root 的挂载'; grep -E ' /root| / ' /proc/mounts | head -3; echo; "
    "echo '## 容器自己的块与 inode'; df -B1 / | tail -1; df -i / | tail -1; echo; "
    "echo '## 我们这次 exec 的会话有没有出现在里面'; find /root/.daytona -maxdepth 2 2>&1 | head -10; echo; "
    "echo '## 谁在跑 daytona 守护进程'; ps aux 2>/dev/null | grep -i daytona | grep -v grep | head -4"
)


async def probe(tid: str, disk: int, sem: asyncio.Semaphore) -> dict:
    rec = {"task_id": tid, "disk_gb": disk, "ts": int(time.time())}
    src = sd.resolve_src(tid)
    if src is None:
        return {**rec, "ok": False, "why": "no_pool_dir"}
    md = sd.pack.to_row(str(src))["metadata"]
    async with sem:
        try:
            async with boot_agent_sandbox(
                md.get("image") or "", dockerfile=md.get("dockerfile") or None,
                build_context=md.get("build_context") or None,
                install_claude=False, cpu=2, memory=2, disk_gb=disk,
            ) as sandbox:
                sb = dr._Root(sandbox)
                code, out, err = await sb.exec(READ, check=False, timeout=120)
                parts = (out or "").split("|")
                return {**rec, "ok": True, "exit": code,
                        "blocks": parts[0].strip() if parts else "",
                        "inodes": parts[1].strip() if len(parts) > 1 else "",
                        "statfs": parts[2].strip() if len(parts) > 2 else "",
                        "mounts": parts[3].strip() if len(parts) > 3 else "",
                        "stderr": (err or "")[-200:]}
        except Exception as e:  # noqa: BLE001
            return {**rec, "ok": False, "why": f"{type(e).__name__}: {str(e)[:200]}"}


async def main_async(a: argparse.Namespace) -> None:
    sem = asyncio.Semaphore(a.concurrency)
    out = Path(a.out)

    async def run(t: str, d: int) -> None:
        try:
            r = await asyncio.wait_for(probe(t, d, sem), timeout=900)
        except asyncio.TimeoutError:
            r = {"task_id": t, "disk_gb": d, "ok": False, "why": "hung past 900s"}
        with open(out, "a") as f:
            f.write(json.dumps(r) + "\n")
        log.info("%s disk=%dGi ok=%s inodes=%s blocks=%s %s", t, d, r.get("ok"),
                 r.get("inodes"), r.get("blocks"), str(r.get("why", ""))[:50])

    await asyncio.gather(*(run(t, d) for t in a.tasks for d in a.disk))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+")
    ap.add_argument("--disk", type=int, nargs="+", default=[2, 10])
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--out", default=str(LOCAL / "session_dir_probe.jsonl"))
    ap.add_argument("--label", default="sessdir_probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(LOCAL / "session_dir_probe.log")])
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
