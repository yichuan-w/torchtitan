# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Validation policies that run through the training Terminus agent."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shlex
import uuid
import time
from dataclasses import asdict

from recorded_solution import parse_actions

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
                # A command may disable ONLCR. Explicit carriage returns keep
                # the result on one line even after such terminal changes.
                f"printf '\\r\\n{self.marker}=%s\\r\\n' \"$?\"\n"
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


# A recording's offsets are the original model's clock, so its thinking time
# sits between the turns: on a live evolve run that was most of a replay (p50
# 120 s, p90 650 s of wall for ~4 s of command waits). Fast replay starts each
# turn once the previous turn's commands are done -- the pane's shell holds the
# terminal again, alone, and its screen is still -- and never later than the
# recorded gap. A turn that left a new background process running keeps its
# recorded gap, since that time may be what let a server come up. "0" restores
# the recorded timing. Only waiting changes; keys, turns and grading do not.
FAST_REPLAY_ENV = "EVOLVE_FAST_REPLAY"
_REPLAY_POLL_SEC = 0.5
_REPLAY_SETTLE_SEC = 0.5
_SHELLS = frozenset({"bash", "sh", "dash", "zsh", "ash", "ksh", "mksh", "busybox"})


def fast_replay_enabled() -> bool:
    return os.environ.get(FAST_REPLAY_ENV, "1") != "0"


def _parse_stat(line: str) -> tuple[int, dict] | None:
    head, sep, tail = line.rpartition(") ")
    pid, _, comm = head.partition(" (")
    fields = tail.split()
    if not sep or not pid.isdigit() or len(fields) < 6:
        return None
    try:
        ppid, pgrp, tpgid = int(fields[1]), int(fields[2]), int(fields[5])
    except ValueError:
        return None
    return int(pid), {
        "comm": comm,
        "state": fields[0],
        "ppid": ppid,
        "pgrp": pgrp,
        "tpgid": tpgid,
    }


def read_terminal(text: str) -> dict:
    """Interpret one probe: is the pane's shell idle, what runs beside it.

    Idle means the terminal's foreground process group is a shell alone and
    sleeping, i.e. waiting for input. Background processes are the pane's live
    descendants plus, when the terminal is isolated, the student cgroup.
    """
    info: dict = {}
    procs: dict[int, dict] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line == "STAT":
            for stat in lines[i + 1 :]:
                parsed = _parse_stat(stat)
                if parsed:
                    procs[parsed[0]] = parsed[1]
            break
        key, _, value = line.partition(" ")
        info[key] = value.strip()
    pane = int(info["PANE"]) if info.get("PANE", "").isdigit() else None
    if pane is None or pane not in procs or "SCREEN" not in info:
        raise ValueError("probe did not identify the pane")
    probe = {int(info["SELF"])} if info.get("SELF", "").isdigit() else set()
    probe |= {pid for pid, p in procs.items() if p["ppid"] in probe}
    live = {pid for pid, p in procs.items() if p["state"] not in ("Z", "X")} - probe
    children: dict[int, list[int]] = {}
    for pid, p in procs.items():
        children.setdefault(p["ppid"], []).append(pid)
    background, todo = set(), list(children.get(pane, []))
    while todo:
        pid = todo.pop()
        if pid not in background:
            background.add(pid)
            todo.extend(children.get(pid, []))
    background |= {int(p) for p in info.get("STUDENT", "").split() if p.isdigit()}
    tpgid = procs[pane]["tpgid"]
    foreground = {pid for pid, p in procs.items() if p["pgrp"] == tpgid} & live
    leader = procs.get(tpgid)
    idle = (
        tpgid > 0
        and leader is not None
        and leader["comm"] in _SHELLS
        and leader["state"] == "S"
        and foreground == {tpgid}
    )
    return {
        "idle": idle,
        "screen": info["SCREEN"],
        "background": frozenset((background & live) - foreground - {pane}),
    }


class TerminalProbe:
    """Read the replay's pane through the sandbox, beside Terminus."""

    def __init__(self, sb, session: str):
        self.sb = sb
        self.session = session
        self.socket = ""

    def script(self) -> str:
        session = shlex.quote(self.session)
        if self.socket:
            find = f"sock={shlex.quote(self.socket)}"
        else:
            # Terminus gives each session's tmux server its own TMUX_TMPDIR;
            # earlier sessions' servers may still be alive beside it.
            find = (
                "sock=; for s in /var/tmp/tmux-*/tmux-*/default "
                "/var/tmp/terminus-*/tmux-*/default; do "
                f'[ -S "$s" ] && tmux -S "$s" has-session -t {session} '
                '2>/dev/null && sock=$s && break; done; [ -n "$sock" ] || exit 3'
            )
        return (
            f"{find}; "
            f'pane=$(tmux -S "$sock" display-message -p -t {session} '
            "'#{pane_pid}') || exit 4; "
            'echo "SOCK $sock"; echo "SELF $$"; echo "PANE $pane"; '
            f'echo "SCREEN $(tmux -S "$sock" capture-pane -p -t {session} | cksum)"; '
            'if grep -q ":/student$" /proc/$pane/cgroup 2>/dev/null; then '
            "echo \"STUDENT $(tr '\\n' ' ' < /sys/fs/cgroup/student/cgroup.procs)\"; "
            "fi; echo STAT; cat /proc/[0-9]*/stat 2>/dev/null; exit 0"
        )

    async def sample(self) -> dict:
        code, out, err = await self.sb.exec(self.script(), check=False, timeout=30)
        if code != 0:
            raise RuntimeError(f"terminal probe exited {code}: {(err or '')[-200:]}")
        state = read_terminal(out or "")
        for line in (out or "").splitlines():
            if line.startswith("SOCK "):
                self.socket = line[5:].strip()
                break
        return state


class RecordedActionsAdapter:
    """Replay terminal turns with their original waits and submit markers.

    Each turn starts at its recorded offset or, given a ``probe`` with fast
    replay on, as soon as the terminal is idle, whichever comes first.
    """

    def __init__(
        self,
        actions: list[dict],
        *,
        probe: TerminalProbe | None = None,
        fast: bool | None = None,
    ):
        self.actions = parse_actions(json.dumps(actions))
        self.index = 0
        self.started = None
        self.marker = "recorded-" + uuid.uuid4().hex
        self.exit_code = None
        self.transcript = []
        self.fast = fast_replay_enabled() if fast is None else fast
        self.probe = probe if self.fast else None
        self.fallback: str | None = None
        self.before: frozenset | None = None
        self.waits: list[dict] = []

    def session_max_tokens(self, _session_id):
        return 24000

    async def _sample(self) -> dict | None:
        try:
            return await self.probe.sample()
        except Exception as exc:  # noqa: BLE001 - fall back to the recorded timing
            self.fallback = f"{type(exc).__name__}: {exc}"[:300]
            self.probe = None
            return None

    async def _wait(self, gap: float) -> str:
        """Wait at most ``gap`` seconds; return why the wait ended."""
        if self.probe is None:
            await asyncio.sleep(gap)
            return "fallback" if self.fallback else "recorded"
        deadline = time.monotonic() + gap
        await asyncio.sleep(min(_REPLAY_SETTLE_SEC, gap))
        previous = None
        while True:
            current = await self._sample()
            if current is None:
                await asyncio.sleep(max(0.0, deadline - time.monotonic()))
                return "fallback"
            if self.before is None:
                self.before = current["background"]
            if (
                previous is not None
                and previous["idle"]
                and current["idle"]
                and previous["screen"] == current["screen"]
            ):
                started = (current["background"] & previous["background"]) - self.before
                self.before = current["background"]
                if not started:
                    return "idle"
                await asyncio.sleep(max(0.0, deadline - time.monotonic()))
                return "background"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.before = current["background"]
                return "gap"
            previous = current
            await asyncio.sleep(min(_REPLAY_POLL_SEC, remaining))

    async def complete(self, _session_id, body):
        if self.index == len(self.actions):
            raise RuntimeError(
                "recorded solution exhausted without confirmed submission"
            )
        if self.started is None:
            self.started = time.monotonic()
        first = self.actions[self.index]
        gap = max(0, first["offset_sec"] - (time.monotonic() - self.started))
        began = time.monotonic()
        reason = await self._wait(gap)
        waited = time.monotonic() - began
        # Offsets stay measured from the recording's start, so a turn that ran
        # early pulls the later ones forward by the time it saved: each later
        # turn is still bounded by its own recorded gap.
        self.started -= max(0.0, gap - waited)
        wait = {"recorded_sec": round(gap, 3), "waited_sec": round(waited, 3)}
        wait["reason"] = reason
        self.waits.append(wait)
        commands = []
        complete = first["kind"] == "completion_marker"
        if complete:
            self.index += 1
            # This means the recording reached submission; grading determines success.
            self.exit_code = 0
        else:
            while self.index < len(self.actions):
                action = self.actions[self.index]
                if (
                    action["kind"] != "terminal_action"
                    or action["step_id"] != first["step_id"]
                    or action["offset_sec"] != first["offset_sec"]
                ):
                    break
                commands.append(
                    {"keystrokes": action["keystrokes"], "duration": action["duration"]}
                )
                self.index += 1
        text = json.dumps(
            {
                "analysis": "Replay the recorded terminal turn.",
                "plan": "Preserve actions and timing.",
                "commands": commands,
                "task_complete": complete,
            }
        )
        self.transcript.append(
            {
                "prompt": body["messages"][-1]["content"],
                "response": text,
                "replay_wait": wait,
            }
        )
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}

    def summary(self) -> dict:
        reasons: dict[str, int] = {}
        for w in self.waits:
            reasons[w["reason"]] = reasons.get(w["reason"], 0) + 1
        return {
            "fast": self.fast,
            # Whether any turn started before its recorded offset. Without one
            # the replay ran at the recorded timing, whatever the setting.
            "fast_used": reasons.get("idle", 0) > 0,
            "fallback": self.fallback,
            "recorded_wait_sec": round(sum(w["recorded_sec"] for w in self.waits), 3),
            "waited_sec": round(sum(w["waited_sec"] for w in self.waits), 3),
            "reasons": reasons,
        }


async def run_reference(
    sb,
    command: str,
    timeout: int,
    *,
    actions: list[dict] | None = None,
    fast: bool | None = None,
) -> dict:
    """Run the oracle through the same setup, parser, terminal and submit loop as training."""
    from torchtitan.experiments.rl.harness.agents.terminus import _PARSER

    if actions is None and _PARSER != "xml":
        raise ValueError("the reference policy requires the training XML parser")
    _, version, _ = await sb.exec("tmux -V", check=True, timeout=30)
    session = "oracle-" + uuid.uuid4().hex
    if actions is None:
        script = "/tmp/tt-reference-" + uuid.uuid4().hex + ".sh"
        # Keep arbitrary command text out of the plain XML action format.
        await sb.write_file(script, command + "\n")
        adapter = ReferenceAdapter("bash " + shlex.quote(script))
    else:
        adapter = RecordedActionsAdapter(
            actions, probe=TerminalProbe(sb, session), fast=fast
        )
    # Waiting consumes episodes too. Allow the full wall budget plus both submit turns.
    max_turns = math.ceil(timeout / 10) + 8
    if actions is not None:
        max_turns = len(actions) + 2
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
            terminal_session_name=session,
            **({"parser_name": "json"} if actions is not None else {}),
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
        **({"replay": adapter.summary()} if actions is not None else {}),
    }
