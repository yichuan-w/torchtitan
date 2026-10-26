# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""mini-swe-agent's text-based loop as a swappable harness (``TMAX_AGENT=miniswe``).

A small reimplementation of ``DefaultAgent`` from mini-swe-agent v1.17.5
(github.com/SWE-agent/mini-swe-agent, commit
68d367bf0ccf40ad59a07101a94c21b66e7798ff, ``src/minisweagent/agents/default.py``)
with its prompts from ``miniswe_prompts.py``. The protocol is upstream's:

  - The model answers with a THOUGHT and exactly one ```bash block. Zero or
    several blocks get ``FORMAT_ERROR_TEMPLATE`` back and the turn runs nothing.
  - The block runs as one independent, non-interactive bash process in the
    sandbox (``Sandbox.exec``, the same path Terminus' setup commands take). No
    shell state survives between actions and there is no terminal, so nothing
    can leave the next action at a heredoc prompt, expand ``!`` from history,
    or wait on a prompt that no key will answer.
  - The observation is ``ACTION_OBSERVATION_TEMPLATE``: the return code and the
    output, head/tail-cut past 10,000 characters. A command killed at the
    per-command timeout gets ``TIMEOUT_TEMPLATE`` instead.
  - Output whose first line is ``COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT``, from a
    command that exited 0, submits. The rollouter then grades the sandbox as
    the agent left it.

What differs from upstream, all because the policy runs here and not behind
litellm on the task's machine:

  - The model is ``adapter.complete`` in-process, as for Terminus and vanillux,
    so every turn is captured for training on the same path.
  - The instance template's ``{{system}} {{release}} {{version}} {{machine}}``
    come from ``uname`` in the sandbox. Terminal-Bench's adapter runs ``mini``
    inside the task container, where ``platform.uname()`` reads the same.
  - Commands start in the image's working directory, where a plain exec lands
    (see ``_start_entrypoint`` in the tmax rollouter); upstream's local
    environment uses the directory ``mini`` was started in.
  - The per-command timeout is ``TMAX_EXEC_TIMEOUT_SEC`` (120 s, the vanillux
    loop's default and what the eval scripts set) instead of upstream's 30 s.
  - Upstream's step and cost limits are replaced by the rollout's own bounds:
    ``AgentTask.time_budget_sec`` (0 = no deadline, as for Terminus) and
    ``AgentTask.max_turns`` (None = TMAX_MINISWE_MAX_TURNS, default 150; 0 is
    no cap, as mini.yaml's ``step_limit: 0``).
    A turn that ends at the context budget ends the episode as
    ``hit_context_limit``, as it does under Terminus.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import time
from typing import Any

from jinja2 import StrictUndefined, Template

from torchtitan.experiments.rl.harness.agents.miniswe_prompts import (
    ACTION_OBSERVATION_TEMPLATE,
    ACTION_REGEX,
    ENV,
    FORMAT_ERROR_TEMPLATE,
    INSTANCE_TEMPLATE,
    SYSTEM_TEMPLATE,
    TIMEOUT_TEMPLATE,
)
from torchtitan.experiments.rl.harness.agents.spec import (
    AgentRun,
    AgentTask,
    FORMAT_ERROR_CATEGORIES,
    register_agent,
)

logger = logging.getLogger(__name__)

# Per-command wall clock, the knob the vanillux loop reads too.
_EXEC_TIMEOUT = int(os.environ.get("TMAX_EXEC_TIMEOUT_SEC", "120"))
# Turn cap when the rollout sets none (training); 0 = uncapped, as upstream.
# 150 stops a format-error loop before the 80K context wall.
_DEFAULT_MAX_TURNS = int(os.environ.get("TMAX_MINISWE_MAX_TURNS", "150"))
# DefaultAgent.has_finished accepts either marker on the output's first line.
_SUBMIT_MARKERS = ("MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
# The sandbox wraps every command in `timeout -s TERM -k <grace> <N>s`: GNU
# timeout exits 124, BusyBox's 143 (the TERM), and 137 if the kill after the
# grace was needed. A command can exit with these itself, so the wall clock must
# also have reached the timeout.
_TIMEOUT_EXIT_CODES = (124, 137, 143)
# See terminus._CONTEXT_TAIL_MARGIN: at the context wall prompt + completion is
# the budget, give or take a rendering token.
_CONTEXT_TAIL_MARGIN = 64
_UNAME = "uname -s; uname -r; uname -v; uname -m"
# A launched command whose exit status never appeared before its deadline (see
# daytona._detached_exec): the command outlived its timeout, typically in a
# stalled sandbox. To the agent that is a command that timed out.
_STATUS_UNAVAILABLE = "daytona exec status unavailable"


@functools.cache
def _template(text: str) -> Template:
    # Upstream's own construction: StrictUndefined, every other option default
    # (so, like upstream, a single trailing newline is dropped).
    return Template(text, undefined=StrictUndefined)


def render(text: str, **variables: Any) -> str:
    return _template(text).render(**variables)


def parse_actions(text: str) -> list[str]:
    """Every ```bash block in ``text`` (DefaultAgent.parse_action's findall)."""
    return re.findall(ACTION_REGEX, text, re.DOTALL)


# TMAX_MINISWE_LENIENT_PARSE=1 runs replies upstream would reject, on the two
# most common shapes seen in the 1024 run's eval (pv50: about 250 replies with a
# lone ```python block, about 230 with two or more ```bash blocks). Off keeps
# upstream's exactly-one-block rule.
_LENIENT_PARSE = os.environ.get("TMAX_MINISWE_LENIENT_PARSE", "1") == "1"
_PYTHON_REGEX = r"```python[ \t]*\n(.*?)\n```"
_PY_HEREDOC = "__MINISWE_PYTHON__"


def lenient_action(text: str, actions: list[str]) -> str | None:
    """One command for a reply without exactly one ```bash block, or None.

    Several ```bash blocks run in order as one script; a reply with none and a
    single ```python block runs it with python3 from a heredoc."""
    if len(actions) > 1:
        return "\n".join(action.strip() for action in actions)
    if not actions:
        python = re.findall(_PYTHON_REGEX, text, re.DOTALL)
        if len(python) == 1 and _PY_HEREDOC not in python[0]:
            return f"python3 - <<'{_PY_HEREDOC}'\n{python[0]}\n{_PY_HEREDOC}"
    return None


def is_submission(output: str, returncode: int) -> bool:
    """DefaultAgent.has_finished: the marker alone on the first output line of a
    command that exited 0. A failed command leaves the agent to retry."""
    lines = output.lstrip().splitlines(keepends=True)
    return bool(lines) and lines[0].strip() in _SUBMIT_MARKERS and returncode == 0


def observation(command: str, output: str, returncode: int, timed_out: bool) -> str:
    """What the model is shown after running ``command``."""
    if timed_out:
        return render(TIMEOUT_TEMPLATE, action={"action": command}, output=output)
    return render(
        ACTION_OBSERVATION_TEMPLATE,
        output={"output": output, "returncode": returncode},
    )


def at_context_wall(reply: dict, max_context: int) -> bool:
    """Whether a capped turn stopped at the context budget rather than at the
    per-turn generation cap (same test as terminus._AdapterLLM.call)."""
    if reply.get("stop_reason") not in ("max_tokens", "length") or max_context <= 0:
        return False
    usage = reply.get("usage") or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    return out_tok == 0 or in_tok + out_tok >= max_context - _CONTEXT_TAIL_MARGIN


class _Episode:
    """One rollout's loop state, kept on an object so that a budget expiry
    mid-turn still reports the turns and commands taken so far."""

    def __init__(self, task: AgentTask) -> None:
        self._task = task
        self._exec_timeout = task.exec_timeout or _EXEC_TIMEOUT
        self.turns = 0
        self.finish_reason = "stopped_early"
        self.exec_trace: list[dict] = []
        # Commands whose exit status never arrived, answered as timeouts.
        self.status_timeouts = 0
        # Replies run under TMAX_MINISWE_LENIENT_PARSE that upstream rejects.
        self.lenient_parses = 0
        # AgentRun.format_breakdown, in the shape the tmax format metrics read.
        # A turn without exactly one block counts as "truncated" when it hit the
        # generation cap, "no_commands" with zero blocks, "other" with several.
        self.categories = dict.fromkeys(FORMAT_ERROR_CATEGORIES, 0)
        self.replies = 0
        self.truncated_turns = 0
        self.repeat_turns = 0
        self.opening_streak = 0
        self.max_streak = 0
        self._streak = 0
        self._acted = False
        self._prev_text: str | None = None
        self.violation_category: str | None = None
        self.violation_turn = 0

    @property
    def format_errors(self) -> int:
        return sum(self.categories.values())

    def breakdown(self) -> dict[str, int]:
        return {
            **self.categories,
            "opening_streak": self.opening_streak,
            "max_streak": self.max_streak,
            "repeat_turns": self.repeat_turns,
            "replies": self.replies,
            "truncated_turns": self.truncated_turns,
            "lenient_parses": self.lenient_parses,
        }

    async def _exec(self, command: str, timeout: int) -> tuple[int, str, float]:
        """Run one command; trace it in the shape the rollout record's ``exec``
        holds (see AgentRun.exec_trace)."""
        started_at = time.time()
        try:
            exit_code, stdout, _stderr = await self._task.sandbox.exec(
                command, env=ENV, timeout=timeout, check=False
            )
        except TimeoutError as e:
            if command == _UNAME or not str(e).startswith(_STATUS_UNAVAILABLE):
                raise
            # Shown as TIMEOUT_TEMPLATE: the wait already exceeded the timeout.
            # A sandbox that is really gone fails the next command instead.
            logger.warning(f"[miniswe] {self._task.session_id}: {e}")
            self.status_timeouts += 1
            exit_code, stdout = 124, ""
        secs = time.time() - started_at
        stats = getattr(self._task.sandbox, "last_exec_stats", None) or {}
        self.exec_trace.append(
            {
                "t": round(started_at, 3),
                "secs": round(secs, 3),
                "exit": exit_code,
                "cmd": command[:400],
                **{
                    key: (round(value, 3) if isinstance(value, float) else value)
                    for key, value in stats.items()
                    if key
                    in ("inline", "launch_s", "wait_s", "read_s", "output_bytes", "truncated")
                },
            }
        )
        # The sandbox folds stderr into stdout, as upstream's subprocess call
        # does; its stderr slot holds only the backend's own timeout note.
        return exit_code, stdout or "", secs

    def _count_reply(self, text: str, capped: bool, n_actions: int) -> None:
        self.replies += 1
        stripped = text.strip()
        self.repeat_turns += self._prev_text is not None and stripped == self._prev_text
        self._prev_text = stripped
        self.truncated_turns += capped
        if n_actions == 1:
            self._streak = 0
            self._acted = True
            return
        category = "truncated" if capped else "no_commands" if n_actions == 0 else "other"
        self.categories[category] += 1
        self._streak += 1
        self.max_streak = max(self.max_streak, self._streak)
        if not self._acted:
            self.opening_streak += 1
        limit = self._task.format_streak_limit
        if limit and self._streak >= limit:
            self.violation_category = category
            self.violation_turn = self.turns

    async def run(self) -> None:
        task = self._task
        exit_code, uname, _secs = await self._exec(_UNAME, 60)
        fields = uname.splitlines()
        if exit_code != 0 or len(fields) != 4:
            raise RuntimeError(f"uname failed (exit={exit_code}): {uname[-400:]!r}")
        system, release, version, machine = fields
        messages: list[dict] = [
            {
                "role": "user",
                "content": render(
                    INSTANCE_TEMPLATE,
                    task=task.instruction,
                    system=system,
                    release=release,
                    version=version,
                    machine=machine,
                ),
            }
        ]
        system_prompt = render(SYSTEM_TEMPLATE)
        max_turns = task.max_turns or _DEFAULT_MAX_TURNS
        while not max_turns or self.turns < max_turns:
            reply = await task.adapter.complete(
                task.session_id,
                # plain_text: the reply as a text-only API returns it, so a
                # stray <tool_call> token cannot hide the ```bash block after it.
                {
                    "system": system_prompt,
                    "messages": messages,
                    "stream": False,
                    "plain_text": True,
                },
            )
            if reply is None:
                # The session is closed or the generator yielded nothing.
                self.finish_reason = "stopped_early"
                return
            self.turns += 1
            blocks = reply.get("content") or []
            # Echo the assistant turn verbatim so the adapter's next prompt
            # extends this one (one packed TITO episode).
            messages.append({"role": "assistant", "content": blocks})
            if at_context_wall(reply, task.max_context_tokens):
                self.finish_reason = "hit_context_limit"
                return
            text = "".join(
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
            capped = reply.get("stop_reason") in ("max_tokens", "length")
            actions = parse_actions(text)
            lenient = None
            if _LENIENT_PARSE and len(actions) != 1:
                lenient = lenient_action(text, actions)
            if lenient is not None:
                self.lenient_parses += 1
                actions = [lenient]
            self._count_reply(text, capped, len(actions))
            if self.violation_turn:
                self.finish_reason = "format_error"
                return
            if len(actions) != 1:
                messages.append(
                    {"role": "user", "content": render(FORMAT_ERROR_TEMPLATE, actions=actions)}
                )
                continue
            command = actions[0].strip()
            exit_code, output, secs = await self._exec(command, self._exec_timeout)
            timed_out = exit_code in _TIMEOUT_EXIT_CODES and secs >= self._exec_timeout
            if not timed_out and is_submission(output, exit_code):
                self.finish_reason = "submit"
                return
            messages.append(
                {
                    "role": "user",
                    "content": observation(command, output, exit_code, timed_out),
                }
            )
        self.finish_reason = "hit_max_turns"


async def miniswe_agent(task: AgentTask) -> AgentRun:
    """Drive the mini-swe-agent loop against the task's sandbox and the adapter."""
    episode = _Episode(task)
    started = time.monotonic()
    # The rollout's wall-clock budget, enforced around the whole loop so a
    # pending generation or command ends as hit_time_budget, before the
    # rollouter's outer guard turns it into an infra failure.
    budget = None
    try:
        async with asyncio.timeout(task.time_budget_sec or None) as budget:
            await episode.run()
    except TimeoutError:
        if budget is None or not budget.expired():
            raise
        episode.finish_reason = "hit_time_budget"
    logger.info(
        "[miniswe] %s: finished after %d turns (finish=%s format_errors=%d "
        "elapsed=%.0fs)",
        task.session_id,
        episode.turns,
        episode.finish_reason,
        episode.format_errors,
        time.monotonic() - started,
    )
    return AgentRun(
        turns=episode.turns,
        submitted=episode.finish_reason == "submit",
        format_errors=episode.format_errors,
        format_breakdown=episode.breakdown(),
        finish_reason=episode.finish_reason,
        violation_category=episode.violation_category,
        violation_turn=episode.violation_turn,
        exec_trace=episode.exec_trace,
    )


register_agent("miniswe", miniswe_agent)
