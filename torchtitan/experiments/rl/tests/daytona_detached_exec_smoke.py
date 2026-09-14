# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Manual smoke of the detached-exec path against one disposable Daytona sandbox.

Run with project Daytona credentials from the training host:

    python daytona_detached_exec_smoke.py --output <new dir>

Creates one 1 CPU / 1 GiB / 1 GiB sandbox from a small public image, drives it
through DaytonaSandbox.exec and deletes it. Cases, each recorded as one JSON
line in <output>/cases.jsonl:

  echo            exit code and output round-trip
  nonzero         exit code 7 comes back as 7
  long            a 150 s command outlives the 30 s launch request
  big_output      2 MiB of output is capped to head/tail with the marker
  daemon          a command that leaves a background daemon still completes
  lost_response   the first launch's response is dropped; the body runs once
  disk_full       exec keeps working with the task disk at 100%
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from daytona import AsyncDaytona, CreateSandboxFromImageParams, Resources

from torchtitan.experiments.rl.harness.sandbox import daytona as daytona_mod
from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox


class LostResponse(ConnectionError):
    status_code = 502


async def run(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    log = (output / "cases.jsonl").open("a", buffering=1)

    def record(case: str, ok: bool, **fields) -> None:
        item = {
            "time": datetime.now(timezone.utc).isoformat(),
            "case": case,
            "ok": ok,
            **fields,
        }
        log.write(json.dumps(item) + "\n")
        print(json.dumps(item), flush=True)

    async with AsyncDaytona() as client:
        sb = await client.create(
            CreateSandboxFromImageParams(
                image="debian:bookworm-slim",
                resources=Resources(cpu=1, memory=1, disk=1),
                labels={"owner": "andy_infra_validation_20260914"},
                ephemeral=True,
                auto_stop_interval=10,
            ),
            timeout=600,
        )
        wrapper = DaytonaSandbox(image="debian:bookworm-slim")
        wrapper._client = client
        wrapper._sb = sb
        wrapper.sandbox_id = sb.id
        try:
            rc, out, _ = await wrapper.exec("echo hi", timeout=30)
            record("echo", rc == 0 and out.strip() == "hi", rc=rc, out=out[:80])

            rc, out, _ = await wrapper.exec("echo bad >&2; exit 7", timeout=30)
            record("nonzero", rc == 7 and "bad" in out, rc=rc, out=out[:80])

            t0 = time.time()
            rc, out, _ = await wrapper.exec(
                "sleep 150; echo slept", timeout=200
            )
            record(
                "long",
                rc == 0 and out.strip() == "slept" and time.time() - t0 >= 150,
                rc=rc,
                secs=round(time.time() - t0),
            )

            # 512 KiB sits under the 1 MiB raw cap, so the tail survives; beyond
            # the raw cap the harness keeps only the first 1 MiB (existing rule).
            rc, out, _ = await wrapper.exec(
                "head -c 524288 /dev/zero | tr '\\0' 'x'; echo; echo END", timeout=60
            )
            record(
                "big_output",
                rc == 0
                and "[torchtitan: command output truncated]" in out
                and out.rstrip().endswith("END")
                and len(out) < 30_000,
                rc=rc,
                out_len=len(out),
                tail=out[-40:],
            )

            t0 = time.time()
            rc, out, _ = await wrapper.exec(
                "(sleep 600 & ) ; echo started-daemon", timeout=30
            )
            record(
                "daemon",
                rc == 0 and out.strip() == "started-daemon" and time.time() - t0 < 30,
                rc=rc,
                secs=round(time.time() - t0, 1),
            )

            real_exec = sb.process.exec
            calls = {"n": 0}

            async def exec_dropping_first_response(*args, **kwargs):
                calls["n"] += 1
                result = await real_exec(*args, **kwargs)
                if calls["n"] == 1:
                    raise LostResponse("injected empty HTTP 502 response")
                return result

            with patch.object(sb.process, "exec", exec_dropping_first_response):
                rc, out, _ = await wrapper.exec(
                    "echo ran >> /dev/shm/lost_count; wc -l < /dev/shm/lost_count",
                    timeout=30,
                )
            counts = wrapper.issue_tracker.counts
            # One launch is enough when the first (lost-response) launch ran the
            # body and its status turned up before the relaunch; two launches
            # are also correct, because the claim makes the second a no-op.
            record(
                "lost_response",
                rc == 0
                and out.strip() == "1"
                and calls["n"] in (1, 2)
                and counts.get("execute_response_recovered") == 1,
                rc=rc,
                out=out.strip(),
                launches=calls["n"],
                issues=dict(counts),
            )

            # dd stops at the first ENOSPC; a second pass in 1 KiB blocks takes
            # the last blocks too. The case is that exec still round-trips.
            await wrapper.exec(
                "dd if=/dev/zero of=/tmp/fill bs=1M 2>/dev/null; "
                "dd if=/dev/zero of=/tmp/fill2 bs=1k 2>/dev/null; true",
                timeout=120,
            )
            rc, out, _ = await wrapper.exec(
                "df / | tail -1; echo probe-rc=$(touch /tmp/probe 2>/dev/null; echo $?)",
                timeout=30,
            )
            record(
                "disk_full",
                rc == 0 and "100%" in out,
                rc=rc,
                out=out.strip()[-160:],
            )
            await wrapper.exec("rm -f /tmp/fill /tmp/fill2", timeout=30)
        finally:
            await client.delete(sb)
            record("deleted", True, sandbox=sb.id)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    asyncio.run(run(Path(args.output)))


if __name__ == "__main__":
    main()
