"""Build every row's Dockerfile on Daytona once and check the result.

For each TB2.1 eval row: boot a sandbox from its Dockerfile (Daytona builds and
caches it), then record `tmux -V`, the default user and working directory, and
whether apt lists are present; compare user/workdir against the original image.

    python mast_rl/prewarm_tb2_tmux_evalset.py DST.jsonl OUT.jsonl [concurrency]
"""

import asyncio
import json
import sys
import time

from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox

PROBE = (
    "echo TMUX=$(tmux -V 2>&1); echo USER=$(id -un); echo HOME=$HOME; "
    "echo APT_LISTS=$(ls /var/lib/apt/lists 2>/dev/null | wc -l)"
)


def _parse(out: str) -> dict:
    kv = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv


async def probe(md: dict, use_dockerfile: bool) -> dict:
    sb = DaytonaSandbox(
        md["image"],
        dockerfile=md["dockerfile"] if use_dockerfile else None,
        cpu=md.get("daytona_cpu"),
        memory=md.get("daytona_mem_gb"),
        disk_gb=md.get("daytona_disk_gb"),
        labels={"run": "tb2-tmux-prewarm"},
    )
    t0 = time.monotonic()
    async with sb:
        boot = time.monotonic() - t0
        _rc, out, _err = await sb.exec(PROBE, timeout=60)
        kv = _parse(out)
    kv["boot_secs"] = round(boot, 1)
    return kv


async def one(md: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        rec = {"task": md["instance_id"]}
        try:
            rec["new"] = await probe(md, True)
        except Exception as e:  # noqa: BLE001
            rec["new_error"] = f"{type(e).__name__}: {e}"[:500]
        try:
            rec["orig"] = await probe(md, False)
        except Exception as e:  # noqa: BLE001
            rec["orig_error"] = f"{type(e).__name__}: {e}"[:500]
        new, orig = rec.get("new", {}), rec.get("orig", {})
        rec["ok"] = new.get("TMUX", "").startswith("tmux ") and new.get(
            "HOME"
        ) == orig.get("HOME")
        print(json.dumps(rec), flush=True)
        return rec


async def main(src: str, out: str, conc: int) -> None:
    rows = [json.loads(line)["metadata"] for line in open(src)]
    sem = asyncio.Semaphore(conc)
    recs = await asyncio.gather(*(one(md, sem) for md in rows))
    with open(out, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    bad = [r["task"] for r in recs if not r["ok"]]
    print(f"DONE {len(recs) - len(bad)}/{len(recs)} ok; not ok: {bad}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 8))
