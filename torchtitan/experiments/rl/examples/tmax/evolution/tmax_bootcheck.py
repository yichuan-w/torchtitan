#!/usr/bin/env python3
"""Boot-check image-based (tmax) rows of the live mix on OUR Daytona fleet.

Per row: create a sandbox from the row's image at the row's declared sizing
(fleet defaults where absent), run a trivial exec, report. This is the
"no Daytona/infra errors" leg of the seed-cleanliness standard for rows whose
images were validated only on someone else's harness.
"""
import asyncio, json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import solve_daytona as sd
from torchtitan.experiments.rl.examples.tmax import layout

ROOT = layout.Root.from_env()
MIX = ROOT.mix.live
OUT = ROOT.path / "results" / "tmax_bootcheck.jsonl"

async def one(md, sem):
    tid = md["instance_id"]
    async with sem:
        t0 = time.time()
        try:
            async with sd.boot_agent_sandbox(
                md.get("image") or "", dockerfile=None, build_context=None,
                install_claude=False,
                cpu=md.get("daytona_cpu"), memory=md.get("daytona_mem_gb"),
                disk_gb=md.get("daytona_disk_gb"),
            ) as sb0:
                sb = sd.dr._Root(sb0)
                rc, out, err = await sb.exec("echo ok && df -h / | tail -1", check=False, timeout=60)
                return {"task_id": tid, "ok": rc == 0, "secs": round(time.time()-t0,1)}
        except Exception as e:
            return {"task_id": tid, "ok": False, "why": f"{type(e).__name__}: {e}"[:140],
                    "secs": round(time.time()-t0,1)}

async def main():
    done = set()
    if os.path.exists(OUT):
        done = {json.loads(l)["task_id"] for l in open(OUT)}
    rows = []
    for l in open(MIX):
        md = json.loads(l)["metadata"]
        if md.get("image") and not md.get("dockerfile") and md["instance_id"].startswith("task_"):
            if md["instance_id"] not in done:
                rows.append(md)
    print(f"boot-checking {len(rows)} image rows (done {len(done)})", flush=True)
    sem = asyncio.Semaphore(200)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a") as f:
        for coro in asyncio.as_completed([one(md, sem) for md in rows]):
            r = await coro
            f.write(json.dumps(r) + "\n"); f.flush()
    ok = sum(1 for l in open(OUT) if json.loads(l)["ok"])
    n = sum(1 for _ in open(OUT))
    print(f"done: {ok}/{n} boot ok")

asyncio.run(main())
