#!/usr/bin/env python3
"""Measure each task's REAL post-build disk use on Daytona, so daytona_disk_gb
stops being a floored underestimate.

est_disk_mb in the dataset only counts the reference solution's writes; it does
NOT count the base image + what the Dockerfile installs (apt-get build-essential,
ansible, ...). Those tasks build to >10 GiB and overflow the floored 10 GiB
sandbox -> BUILD_FAILED / "no space left". Metadata cannot signal this (there is
no image-size field), so measure it: boot each task with a GENEROUS disk, build,
read actual `df` usage, and recommend daytona_disk_gb = used * 1.3 (headroom for
the agent's own installs at solve time).

Distinguishes disk-overflow (fixable by a bigger disk) from a genuine build
failure (Dockerfile broken -- needs repair, not disk): a task that still fails
to build with the generous disk is reported built=false.

Runs at MAX concurrency (auto_delete=15min keeps the account clean).

  measure_disk.py --ids ids.txt --out results/disk.jsonl --concurrency 200 --boot-gb 40
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
from pathlib import Path

import solve_daytona as sd  # reuses resolve_src + the harness boot path

BASE = sd.BASE
log = logging.getLogger("measure_disk")


async def measure_one(tid: str, boot_gb: int, sem: asyncio.Semaphore) -> dict:
    src = sd.resolve_src(tid)
    if src is None:
        return {"task_id": tid, "built": False, "why": "no_pool_dir"}
    try:
        row = sd.pack.to_row(str(src))
    except Exception as e:  # noqa: BLE001
        return {"task_id": tid, "built": False, "why": f"row:{e}"[:120]}
    md = row["metadata"]
    workdir = md.get("workdir") or "/workspace"
    async with sem:
        try:
            # boot with a generous disk so the build has room to complete;
            # what we want is the true footprint, not whether 10 GiB fit.
            async with sd.boot_agent_sandbox(
                md.get("image") or "",
                dockerfile=md.get("dockerfile") or None,
                build_context=md.get("build_context") or None,
                install_claude=False, disk_gb=boot_gb,
            ) as sandbox:
                sb = sd.dr._Root(sandbox)
                # bytes used on the filesystem holding the workdir
                # du of the overlay's merged view, not df: overlay df "Used"
                # REAL block usage, not apparent size (-b implies
                # --apparent-size): a task whose subject is `truncate -s 10G
                # /disk.img` allocates ~no blocks but shows 10GB apparent --
                # tw_627786/tw_693888 read as 11/21GB and got branded
                # tier-unrunnable while demonstrably building inside 10GB.
                # counts only the upper layer (post-boot writes; measured 36KB
                # on a task whose image holds 84MB), while summing df rows
                # drags in host bind mounts (/etc/hosts on a 197GB host disk).
                # -x stays on one filesystem; add workdir separately when it
                # is a different device (a volume).
                rc, out, err = await sb.exec(
                    f"U=$(du -sx --block-size=1 / 2>/dev/null | cut -f1); W=0; "
                    f"[ \"$(stat -c %d / 2>/dev/null)\" != "
                    f"\"$(stat -c %d {workdir} 2>/dev/null)\" ] && "
                    f"W=$(du -sx --block-size=1 {workdir} 2>/dev/null | cut -f1); "
                    f"echo $((U+${{W:-0}}))",
                    check=False, timeout=300)
                used = None
                for tok in (out or "").split():
                    if tok.isdigit():
                        used = int(tok); break
                used_mb = round(used / 1e6) if used else None
                rec_gb = math.ceil(used / 1e9 * 1.3) if used else None
                return {"task_id": tid, "built": True, "boot_gb": boot_gb,
                        "disk_used_mb": used_mb,
                        "est_disk_mb": None,  # filled below from metadata if present
                        # Floor at the 1/2/2-era fleet default, NOT 10: a 10GB
                        # floor would make the resource backfill set 10GB on
                        # every row and waste the account's disk budget.
                        "recommend_daytona_gb": max(rec_gb or 0, 2)}
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            kind = ("no_space" if "no space" in msg.lower() else
                    "build_failed" if "BUILD_FAILED" in msg or "build" in msg.lower() else
                    "boot_error")
            return {"task_id": tid, "built": False, "why": msg[:160], "kind": kind}


async def run(args) -> None:
    ids = [l.strip() for l in open(args.ids) if l.strip()]
    done = set()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        for l in open(out):
            if l.strip():
                done.add(json.loads(l)["task_id"])
    todo = [t for t in ids if t not in done]
    log.info("ids=%d done=%d todo=%d concurrency=%d boot_gb=%d",
             len(ids), len(done), len(todo), args.concurrency, args.boot_gb)
    sem = asyncio.Semaphore(args.concurrency)
    built = overflow = failed = 0
    with open(out, "a") as f:
        for i in range(0, len(todo), args.batch):
            batch = todo[i:i + args.batch]
            recs = await asyncio.gather(
                *[measure_one(t, args.boot_gb, sem) for t in batch])
            for r in recs:
                f.write(json.dumps(r) + "\n")
                if r.get("built"):
                    built += 1
                    if (r.get("recommend_daytona_gb") or 0) > 10:
                        overflow += 1
                        log.info("%s needs %sGB (used %sMB)", r["task_id"],
                                 r["recommend_daytona_gb"], r.get("disk_used_mb"))
                else:
                    failed += 1
            f.flush()
            log.info("progress %d/%d  built=%d over-10GB=%d failed=%d",
                     min(i + args.batch, len(todo)), len(todo), built, overflow, failed)
    print(f"\n=== disk summary ===\nbuilt={built} need>10GB={overflow} failed-to-build={failed}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", default=str(BASE / "results/disk.jsonl"))
    ap.add_argument("--concurrency", type=int, default=200)
    ap.add_argument("--boot-gb", type=int, default=40)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--log", default=str(BASE / "logs/measure_disk.log"))
    args = ap.parse_args()
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()])
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
