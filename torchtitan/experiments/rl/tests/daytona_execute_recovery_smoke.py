# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Manual fault injection against one disposable Daytona sandbox.

Run with project Daytona credentials and --output pointing to a new directory.
This creates one 1-CPU sandbox, installs tmux, and deletes the sandbox afterward.
No model endpoint or corpus task is used.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from daytona import AsyncDaytona, CreateSandboxFromImageParams, Resources

from torchtitan.experiments.rl.harness.agents.spec import AgentTask
from torchtitan.experiments.rl.harness.agents.terminus import terminus_agent
from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox


class LostResponse(ConnectionError):
    status_code = 502


async def run(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)

    def event(kind: str, **fields) -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **fields,
        }
        with (output / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        print(json.dumps(record), flush=True)

    inputs = {
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "versions": {
            name: importlib.metadata.version(name) for name in ("daytona", "harbor")
        },
        "image": "ubuntu:24.04",
        "cpu": 1,
        "memory": 1,
        "disk": 3,
        "ttl_minutes": 10,
    }
    (output / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    event("create", **inputs)
    client = AsyncDaytona()
    sandbox = await client.create(
        CreateSandboxFromImageParams(
            image=inputs["image"],
            resources=Resources(cpu=1, memory=1, disk=3),
            auto_stop_interval=1,
            auto_delete_interval=0,
            ttl_minutes=10,
        ),
        timeout=180,
    )
    wrapper = DaytonaSandbox("execute-recovery-smoke")
    wrapper._client, wrapper._sb, wrapper.sandbox_id = client, sandbox, sandbox.id
    event("created", sandbox_id=sandbox.id)
    original_execute = sandbox.process.execute_session_command
    original_exec = wrapper.exec
    pending = []
    try:
        identity = await wrapper.exec("id -u", check=True)
        assert identity[1].strip() == "0", identity
        installed = await sandbox.process.exec(
            "apt-get update && apt-get install -y tmux", timeout=120
        )
        event("tmux_install", exit_code=installed.exit_code, output=installed.result)
        assert installed.exit_code == 0
        for name in (
            "before_accept",
            "after_accept",
            "delayed_accept",
            "capture",
            "send",
        ):
            event("case_start", case=name)
            calls = []
            armed = name not in ("capture", "send")
            injected = False
            marker = f"/var/tmp/{name}.count"

            async def delayed_execute(*args, **kwargs):
                await asyncio.sleep(5)
                return await original_execute(*args, **kwargs)

            async def execute(*args, **kwargs):
                nonlocal injected
                if armed:
                    calls.append({"session_id": args[0], "command": args[1].command})
                    if not injected:
                        injected = True
                        if name == "delayed_accept":
                            pending.append(
                                asyncio.create_task(delayed_execute(*args, **kwargs))
                            )
                        elif name != "before_accept":
                            await original_execute(*args, **kwargs)
                        raise LostResponse("injected empty HTTP 502 response")
                return await original_execute(*args, **kwargs)

            async def hidden_session(*args, **kwargs):
                return SimpleNamespace(commands=[])

            async def terminal_exec(command, *args, **kwargs):
                nonlocal armed
                target = "capture-pane" if name == "capture" else "send-keys"
                armed = target in command
                return await original_exec(command, *args, **kwargs)

            command = f"printf x >> {marker}; sleep 4; printf original; exit 7"
            transcript = []

            class Adapter:
                def session_max_tokens(self, session):
                    return 1024

                async def complete(self, session, body):
                    step = len(transcript)
                    action = (
                        f'<keystrokes duration="0.3">printf x >> {marker}; PROBE=kept; cd /var\n</keystrokes>'
                        if step == 0
                        else (
                            '<keystrokes duration="0.3">printf "STATE=%s CWD=%s\\n" "$PROBE" "$PWD"\n</keystrokes>'
                            if step == 1
                            else ""
                        )
                    )
                    response = (
                        "<response><analysis>Check recovery.</analysis><plan>Run the check.</plan>"
                        f"<commands>{action}</commands><task_complete>{str(step >= 2).lower()}</task_complete></response>"
                    )
                    transcript.append(
                        {
                            "prompt": body["messages"][-1]["content"],
                            "response": response,
                        }
                    )
                    return {
                        "content": [{"type": "text", "text": response}],
                        "stop_reason": "end_turn",
                    }

            with patch.object(
                sandbox.process, "execute_session_command", execute
            ), patch.object(sandbox.process, "get_session", hidden_session):
                if name in ("capture", "send"):
                    with patch.object(wrapper, "exec", terminal_exec):
                        result = await terminus_agent(
                            AgentTask(
                                sandbox=wrapper,
                                instruction="Run the supplied terminal checks.",
                                session_id=name,
                                adapter=Adapter(),
                                time_budget_sec=120,
                                max_turns=6,
                            ),
                            terminal_session_name=name,
                        )
                    assert result.submitted and result.finish_reason == "submit", result
                    assert any(
                        "STATE=kept CWD=/var" in item["prompt"] for item in transcript
                    )
                    result = asdict(result)
                else:
                    result = await wrapper.exec(command, timeout=30, check=False)
                    assert result == (7, "original"), result
            if pending:
                await asyncio.gather(*pending)
                pending.clear()
            count = await wrapper.exec(f"cat {marker}", check=True)
            assert count == (0, "x"), count
            assert injected
            if name in ("before_accept", "delayed_accept"):
                assert len(calls) >= 2 and all(call == calls[0] for call in calls)
            receipt = {
                "case": name,
                "input": command,
                "result": result,
                "count": count,
                "submissions": calls,
                "transcript": transcript,
                "status": "passed",
            }
            (output / f"{name}.json").write_text(json.dumps(receipt, indent=2) + "\n")
            event("case_passed", case=name, submissions=len(calls))
    finally:
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await wrapper.__aexit__(None, None, None)
        for _ in range(30):
            try:
                await client.get(sandbox.id)
            except Exception as exc:
                if getattr(exc, "status_code", getattr(exc, "status", None)) != 404:
                    raise
                event("cleanup_verified", status=404)
                break
            await asyncio.sleep(1)
        else:
            raise AssertionError("sandbox deletion not confirmed")
    event("passed", cases=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args().output))
