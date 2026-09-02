#!/usr/bin/env python3
"""Boot a Dockerfile under Daytona and run one command in it.

Used to answer the question a repair plan rests on before writing the repair:
tw_157216's local-chain fix needs the Stellar quickstart image to build as a
Daytona snapshot and to have Horizon answering inside the sandbox. That is a
384 MB image with a supervisor entrypoint, which is not like anything else in
the corpus, so it gets tried on its own before the task is touched.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

if "--label" in sys.argv:
    os.environ["TT_DAYTONA_LABEL"] = sys.argv[sys.argv.index("--label") + 1]
else:
    os.environ.setdefault("TT_DAYTONA_LABEL", "image_probe")

import daytona_revalidate as dr  # noqa: E402
from torchtitan.experiments.rl.harness.agents.claude_code import boot_agent_sandbox  # noqa: E402


async def run(a: argparse.Namespace) -> dict:
    df = Path(a.dockerfile)
    rec = {"dockerfile": str(df), "ts": int(time.time())}
    t0 = time.time()
    try:
        async with boot_agent_sandbox(
            # build_context is a {path: content} mapping, not a directory; this
            # probe's Dockerfile has no COPY, so it needs none.
            "", dockerfile=df.read_text(), build_context=None,
            install_claude=False, cpu=a.cpu, memory=a.mem, disk_gb=a.disk,
        ) as sandbox:
            rec["boot_secs"] = round(time.time() - t0, 1)
            sb = dr._Root(sandbox)
            if a.entrypoint:
                await dr._start_entrypoint(sb, a.entrypoint, workdir="/app")
            code, out, err = await sb.exec(a.command, check=False, timeout=a.timeout)
            return {**rec, "ok": True, "exit": code,
                    "out": (out or "")[-1500:], "err": (err or "")[-400:],
                    "total_secs": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {**rec, "ok": False, "secs": round(time.time() - t0, 1),
                "why": f"{type(e).__name__}: {str(e)[:300]}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dockerfile")
    ap.add_argument("--command", default="echo alive")
    # Reading the command from a file rather than the command line: these probes
    # carry shell inside shell inside ssh, and the quoting has eaten three of
    # them already.
    ap.add_argument("--command-file", default="")
    ap.add_argument("--entrypoint", default="")
    ap.add_argument("--cpu", type=int, default=2)
    ap.add_argument("--mem", type=int, default=4)
    ap.add_argument("--disk", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--label", default="image_probe")
    a = ap.parse_args()
    if a.command_file:
        a.command = Path(a.command_file).read_text()
    print(json.dumps(asyncio.run(run(a)), ensure_ascii=False))


if __name__ == "__main__":
    main()
