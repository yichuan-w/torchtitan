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
  output_complete_under_cap   512 KiB comes back whole, no marker
  output_head_marker_tail     12 MiB comes back as 4 MiB + marker + true last 4 MiB
  daemon          a command that leaves a background daemon still completes
  lost_response   the first launch's response is dropped; the body runs once
  observation     a real tmux: first capture is the screen, then new output by
                  offset, clear and a pager fall back to the screen
  disk_full       exec keeps working with the task disk at 100%
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from types import SimpleNamespace
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

            # Under 8 MiB the caller gets the complete output, no marker.
            rc, out, _ = await wrapper.exec(
                "head -c 524288 /dev/zero | tr '\\0' 'x'; echo; echo END", timeout=60
            )
            record(
                "output_complete_under_cap",
                rc == 0
                and "[torchtitan: command output truncated]" not in out
                and out.rstrip().endswith("END")
                and len(out) == 524288 + 1 + 4,
                rc=rc,
                out_len=len(out),
            )

            # Over 8 MiB: the first 4 MiB, the marker, and the TRUE last 4 MiB.
            rc, out, _ = await wrapper.exec(
                "yes xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx "
                "| head -c 12582912; echo; echo END",
                timeout=120,
            )
            marker = "[torchtitan: command output truncated]"
            record(
                "output_head_marker_tail",
                rc == 0
                and marker in out
                and out.rstrip().endswith("END")
                and 8 * 1024 * 1024 - 4096 < len(out) < 8 * 1024 * 1024 + 4096,
                rc=rc,
                out_len=len(out),
                marker_at=out.find(marker),
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

            # --- the turn observation against a real tmux, no model involved ---
            from torchtitan.experiments.rl.harness.agents.terminus import (
                _SandboxEnvironment,
            )

            rc, out, _ = await wrapper.exec(
                "apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq tmux less "
                ">/dev/null 2>&1; tmux -V",
                timeout=300,
            )
            env = _SandboxEnvironment(wrapper, agent_dir=Path("/tmp/agent"))
            await env.terminal.prepare()
            await wrapper.exec(
                f"TMUX_TMPDIR={env.terminal.directory} tmux new-session -d -s obs -x 160 -y 40 'bash --login'",
                timeout=30,
            )
            await env.terminal.bind("obs")
            session = SimpleNamespace(_session_name="obs")

            async def turn(keys: str, wait: float = 1.0, enter: bool = True) -> str:
                await env.exec(f"tmux send-keys -t obs -- {keys}{' Enter' if enter else ''}")
                await asyncio.sleep(wait)
                return await env.observe_turn(session)

            first = await turn("'seq 1 100'")
            second = await turn("'echo NEW-A; echo NEW-B'")
            third = await turn("clear")
            fourth = await turn("'seq 1 300 | less'")
            # Quitting the pager is itself a turn: keys, wait, observe, as
            # Terminus-2 does; the observation consumes the turn's start note.
            quit_pager = await turn("q", wait=0.5, enter=False)
            fifth = await turn("'echo AFTER-LESS'")
            obs = [e for e in env.exec_trace if e.get("kind") == "observation"]
            record(
                "observation",
                first.startswith("Current Terminal Screen:")
                and second.startswith("New Terminal Output:")
                and "NEW-A" in second
                and "NEW-B" in second
                and "seq 1 100" not in second.split("NEW-A")[0].split("\n", 1)[-1]
                and third.startswith("Current Terminal Screen:")
                and fourth.startswith("Current Terminal Screen:")
                and quit_pager.startswith("Current Terminal Screen:")
                and fifth.startswith("New Terminal Output:")
                and "AFTER-LESS" in fifth,
                shown=[o["shown"] for o in obs],
                modes=[o["mode"] for o in obs],
                second_head=second[:120],
                fifth_head=fifth[:120],
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
