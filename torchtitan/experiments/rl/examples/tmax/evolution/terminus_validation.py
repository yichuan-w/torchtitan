"""Validation policies that run through the training Terminus agent."""

from __future__ import annotations

import asyncio
import math
import re
import shlex
import uuid
from dataclasses import asdict

from torchtitan.experiments.rl.harness.agents.spec import AgentTask
from torchtitan.experiments.rl.harness.agents.terminus import terminus_agent


class ReferenceExecutionError(RuntimeError):
    def __init__(self, cause, transcript):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.validation = {"execution_harness": "terminus", "transcript": transcript}


class ReferenceAdapter:
    """Send one command, observe its exit in the terminal, then confirm submission."""

    def __init__(self, command: str):
        self.marker = "TT_REFERENCE_EXIT_" + uuid.uuid4().hex
        self.command = command
        self.started = False
        self.exit_code = None
        self.transcript = []

    def session_max_tokens(self, _session_id):
        return 1024

    async def complete(self, _session_id, body):
        prompt = body["messages"][-1]["content"]
        if self.started and self.exit_code is None:
            # Match a result line, never the shell's echo of the printf command.
            match = re.search(r"(?m)^" + self.marker + r"=([0-9]{1,3})\r?$", prompt)
            if match:
                self.exit_code = int(match.group(1))
        if not self.started:
            keys = (
                f"bash -c {shlex.quote(self.command)}; "
                f"printf '\\n{self.marker}=%s\\n' \"$?\"\n"
            )
            duration = 1
            self.started = True
        else:
            keys = ""
            duration = 10
        complete = self.exit_code is not None
        commands = (
            "" if complete else f'<keystrokes duration="{duration}">{keys}</keystrokes>'
        )
        text = (
            "<response><analysis>Observe the reference command.</analysis>"
            "<plan>Wait for its exit status, then submit.</plan>"
            f"<commands>{commands}</commands>"
            f"<task_complete>{str(complete).lower()}</task_complete></response>"
        )
        self.transcript.append({"prompt": prompt, "response": text})
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


class ProviderAdapter:
    """Translate provider completions without replacing Terminus's prompt or actions."""

    def __init__(self, provider):
        self.provider = provider
        self.transcript = []

    def session_max_tokens(self, _session_id):
        return 24000 if self.provider.EFFORT == "high" else 12000

    async def complete(self, _session_id, body):
        response = await asyncio.to_thread(
            self.provider.chat_response, body["messages"], self.session_max_tokens(None)
        )
        choice = response["choices"][0]
        text = choice["message"]["content"] or ""
        usage = response.get("usage") or {}
        self.transcript.append(
            {
                "prompt": body["messages"][-1]["content"],
                "response": text,
                "model": response.get("model"),
                "usage": usage,
                "finish_reason": choice.get("finish_reason"),
            }
        )
        return {
            "content": [{"type": "text", "text": text}],
            "stop_reason": choice.get("finish_reason"),
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        }


async def run_reference(sb, command: str, timeout: int) -> dict:
    """Run the oracle through the same setup, parser, terminal and submit loop as training."""
    from torchtitan.experiments.rl.harness.agents.terminus import _PARSER

    if _PARSER != "xml":
        raise ValueError("the reference policy requires the training XML parser")
    _, version, _ = await sb.exec("tmux -V", check=True, timeout=30)
    script = "/tmp/tt-reference-" + uuid.uuid4().hex + ".sh"
    # Keep arbitrary command text out of the plain XML action format.
    await sb.write_file(script, command + "\n")
    adapter = ReferenceAdapter("bash " + shlex.quote(script))
    # Waiting consumes episodes too. Allow the full wall budget plus both submit turns.
    max_turns = math.ceil(timeout / 10) + 8
    try:
        run = await terminus_agent(
            AgentTask(
                sandbox=sb,
                instruction="Execute the supplied reference command.",
                session_id=adapter.marker,
                adapter=adapter,
                time_budget_sec=timeout,
                max_turns=max_turns,
            ),
            terminal_session_name="oracle-" + uuid.uuid4().hex,
        )
    except Exception as exc:
        raise ReferenceExecutionError(exc, adapter.transcript) from exc
    pane_error = None
    try:
        pane = await sb.read_file(run.pane_path) if run.pane_path else ""
        if not pane:
            pane_error = "terminal log is missing or empty"
    except Exception as exc:
        pane = ""
        pane_error = f"{type(exc).__name__}: {exc}"
    submitted = run.submitted is True and run.finish_reason == "submit"
    code = adapter.exit_code if submitted else None
    if code is None and run.finish_reason == "hit_time_budget":
        code = 124
    return {
        "solve_exit": code,
        "submitted": submitted,
        "stdout": pane,
        "stderr": "",
        "pane_error": pane_error,
        "transcript": adapter.transcript,
        "terminal": {
            **asdict(run),
            "version": version.strip(),
            "max_turns": max_turns,
            "time_budget_sec": timeout,
            "pane_limit_bytes": 8 * 1024 * 1024,
            "exec_command_limit_chars": 400,
        },
        "execution_harness": "terminus",
    }
