#!/usr/bin/env python3
"""Read inodes, not just bytes, on the images that actually fail.

`mkdir` returns ENOSPC when the filesystem is out of blocks *or* out of inodes,
and the second explains what the first cannot: session-create failures cluster
on particular tasks, recur at 10 GiB where a sub-gigabyte image cannot fill the
box, and hit whole groups of that task's rollouts at once. An inode budget is
fixed by the filesystem rather than scaled with the requested disk, so a heavy
image exhausts it identically at 2 GiB and at 10.

The earlier inode check said 2-3% used and was run against a task that never
failed -- the same sampling mistake that produced the wrong verdict on the
byte-level question. This one boots the tasks the logs blame.
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
    os.environ.setdefault("TT_DAYTONA_LABEL", "inode_probe")

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
    "df -B1 / | tail -1; echo '|'; "
    "df -i / | tail -1; echo '|'; "
    "stat -f -c '%s %b %f %a %c %d' / ; echo '|'; "
    "cat /proc/mounts | grep -E ' / | /root ' | head -3"
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
    ap.add_argument("--out", default=str(LOCAL / "inode_probe.jsonl"))
    ap.add_argument("--label", default="inode_probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(LOCAL / "inode_probe.log")])
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
