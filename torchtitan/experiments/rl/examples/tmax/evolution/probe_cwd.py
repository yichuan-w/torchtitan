#!/usr/bin/env python3
"""Where does the model actually stand, and where does the oracle stand?

The per-task `workdir` is ours: no task.toml declares one, and data prep fills
in /app (or /workspace, depending on the corpus) when the Dockerfile has no
WORKDIR. Terminus never reads it -- its tmux pane starts wherever the image
lands a shell -- while the oracle paths cd into it explicitly. That is two
different working directories for the same task, and the difference is what
broke tw_266088 on the two runners whose cd had no fallback.

Before deciding whether to fix the cd or delete it, this measures the three
places a command can land: a plain exec, a shell inside a tmux pane started the
way terminus starts one, and the directory data prep guessed. If the first two
agree, the oracle should stop cd-ing rather than cd better.
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
    os.environ.setdefault("TT_DAYTONA_LABEL", "cwd_probe")

import daytona_revalidate as dr  # noqa: E402
from torchtitan.experiments.rl.harness.agents.claude_code import boot_agent_sandbox  # noqa: E402

LOCAL = Path(os.environ.get("MEASURE_LOCAL_BASE", "/scratch/al9080/terminal-rl/measure"))
log = logging.getLogger("probe_cwd")

READ = (
    "echo \"PLAIN=$(pwd)\"; "
    # Started the way terminus starts its pane, then asked where it stands.
    "tmux kill-server 2>/dev/null; "
    "tmux new-session -d -s probe 'sleep 30' 2>/dev/null && sleep 1 && "
    "echo \"TMUX=$(tmux display-message -p -t probe '#{pane_current_path}' 2>/dev/null)\"; "
    "echo \"IMAGE_WORKDIR=$(docker 2>/dev/null; echo -n)\"; "
    "echo \"HOME=$HOME\"; "
    "echo \"ROOTDIRS=$(ls -d /app /workspace /home/user 2>/dev/null | tr '\\n' ' ')\""
)


async def probe(tid: str, md: dict, sem: asyncio.Semaphore) -> dict:
    rec = {"task_id": tid, "declared_workdir": md.get("workdir"), "ts": int(time.time())}
    async with sem:
        try:
            async with boot_agent_sandbox(
                md.get("image") or "", dockerfile=md.get("dockerfile") or None,
                build_context=md.get("build_context") or None,
                install_claude=False, cpu=2, memory=2, disk_gb=4,
            ) as sandbox:
                sb = dr._Root(sandbox)
                _, out, err = await sb.exec(READ, check=False, timeout=180)
                got = {}
                for line in (out or "").splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        got[k.strip()] = v.strip()
                return {**rec, "ok": True, **got, "raw": (out or "")[-300:]}
        except Exception as e:  # noqa: BLE001
            return {**rec, "ok": False, "why": f"{type(e).__name__}: {str(e)[:200]}"}


async def main_async(a: argparse.Namespace) -> None:
    meta = {}
    for line in open(a.mix):
        if line.strip():
            m = json.loads(line)["metadata"]
            meta[m.get("instance_id")] = m
    sem = asyncio.Semaphore(a.concurrency)
    out = Path(a.out)

    async def run(t: str) -> None:
        if t not in meta:
            return
        try:
            r = await asyncio.wait_for(probe(t, meta[t], sem), timeout=900)
        except asyncio.TimeoutError:
            r = {"task_id": t, "ok": False, "why": "hung"}
        with open(out, "a") as f:
            f.write(json.dumps(r) + "\n")
        log.info("%s declared=%s plain=%s tmux=%s", t, r.get("declared_workdir"),
                 r.get("PLAIN"), r.get("TMUX"))

    await asyncio.gather(*(run(t) for t in a.tasks))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+")
    ap.add_argument("--mix",
                    default="/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/mix_live.jsonl")
    ap.add_argument("--out", default=str(LOCAL / "cwd_probe.jsonl"))
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--label", default="cwd_probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(LOCAL / "cwd_probe.log")])
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
