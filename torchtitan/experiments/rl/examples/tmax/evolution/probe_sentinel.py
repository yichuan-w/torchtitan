#!/usr/bin/env python3
"""Ask every task whether the grader can read back what it just wrote.

grade_tmax writes a nonce into the reward path, reads it back, and compares with
exact equality -- a guard so a task cannot be paid for a reward its verifier
never wrote. But read_file is an exec, and the harness merges stdout with
stderr, so anything the image prints when a shell starts arrives ahead of the
nonce and the comparison fails. The task then scores 0 before its verifier runs
at all, which in a training curve is indistinguishable from a policy that could
not solve it.

tw_419317 is one: fedora:33 declares LC_ALL=en_US.utf-8 and ships only C.utf8,
so every bash prints a setlocale warning. Counting Dockerfiles that declare a
locale finds that one and stops there, because the noise can come from anything
a login shell touches. So this reproduces the round-trip itself against every
image and reports which ones come back dirty.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

if "--label" in sys.argv:
    os.environ["TT_DAYTONA_LABEL"] = sys.argv[sys.argv.index("--label") + 1]
else:
    os.environ.setdefault("TT_DAYTONA_LABEL", "sentinel_probe")

import daytona_revalidate as dr  # noqa: E402
from torchtitan.experiments.rl.harness.agents.claude_code import boot_agent_sandbox  # noqa: E402

LOCAL = Path(os.environ.get("MEASURE_LOCAL_BASE", "/scratch/al9080/terminal-rl/measure"))
log = logging.getLogger("probe_sentinel")
REWARD_PATH = "/logs/verifier/reward.txt"


async def probe(tid: str, cpu: int, mem: int, disk: int,
                sem: asyncio.Semaphore, md: dict) -> dict:
    rec = {"task_id": tid, "ts": int(time.time())}
    if not (md.get("image") or md.get("dockerfile")):
        return {**rec, "ok": False, "why": "no_image"}
    workdir = md.get("workdir") or "/app"
    nonce = f"tmax-sentinel-{uuid.uuid4().hex}"
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
                await sb.exec(f"mkdir -p $(dirname {REWARD_PATH})", check=False, timeout=60)
                await sb.write_file(REWARD_PATH, nonce)
                # Exactly what grade_tmax does, so a mismatch here is a
                # mis-scored rollout there and not an artefact of this probe.
                got = (await sb.read_file(REWARD_PATH, user="root") or "").strip()
                # A bare shell with no file involved, to name the noise itself.
                _, echo, _ = await sb.exec("printf ok", check=False, timeout=60)
                return {**rec, "ok": True, "clean": got == nonce,
                        "got": got[:300], "nonce": nonce,
                        "bare_echo": (echo or "")[:300],
                        "echo_clean": (echo or "").strip() == "ok",
                        "image": (md.get("image") or "")[:80]}
        except Exception as e:  # noqa: BLE001
            return {**rec, "ok": False, "why": f"{type(e).__name__}: {str(e)[:200]}"}


async def main_async(a: argparse.Namespace) -> None:
    sizes = {}
    for line in open(a.sizing):
        if line.strip():
            r = json.loads(line)
            sizes[r["task_id"]] = (r["cpu"], r["mem_gb"], r["disk_gb"])
    # The mix carries the image for both halves; TMax ships no package
    # directory, so resolving through the pool would skip 400 tasks whose
    # images can be just as noisy.
    meta = {}
    for line in open(a.mix):
        if line.strip():
            m = json.loads(line)["metadata"]
            meta[m.get("instance_id")] = m
    out = Path(a.out)
    done = set()
    if out.exists() and not a.overwrite:
        for line in open(out):
            if line.strip():
                done.add(json.loads(line)["task_id"])
    todo = [t for t in sorted(sizes) if t not in done and t in meta]
    if a.limit:
        todo = todo[: a.limit]
    log.info("%d 题待测", len(todo))
    sem = asyncio.Semaphore(a.concurrency)
    lock = asyncio.Lock()
    n = [0]

    async def run(t: str) -> None:
        c, m, d = sizes[t]
        try:
            r = await asyncio.wait_for(probe(t, c, m, d, sem, meta[t]),
                                       timeout=a.budget)
        except asyncio.TimeoutError:
            r = {"task_id": t, "ok": False, "why": f"hung past {a.budget}s"}
        async with lock:
            with open(out, "a") as f:
                f.write(json.dumps(r) + "\n")
            n[0] += 1
            if r.get("ok") and not r.get("clean"):
                log.warning("[%d/%d] %s 脏: %r", n[0], len(todo), t, r.get("got", "")[:120])
            elif n[0] % 50 == 0:
                log.info("[%d/%d] ...", n[0], len(todo))

    await asyncio.gather(*(run(t) for t in todo))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizing", default=str(LOCAL / "sizing_both_v3.jsonl"))
    ap.add_argument("--mix",
                    default="/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/mix_live.jsonl")
    ap.add_argument("--out", default=str(LOCAL / "sentinel_probe.jsonl"))
    ap.add_argument("--concurrency", type=int, default=48)
    # Tasks carrying a dockerfile are built server-side on first boot, and at
    # high concurrency the queue alone can outlast a short budget: the first
    # sweep timed out on 267 of them and reported a clean result for a corpus
    # it had not finished measuring. Lower the concurrency and raise this.
    ap.add_argument("--budget", type=int, default=900)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--label", default="sentinel_probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(LOCAL / "sentinel_probe.log")])
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
