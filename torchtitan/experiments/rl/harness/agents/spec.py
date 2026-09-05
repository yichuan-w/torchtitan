# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""One contract every agent harness answers to, plus the registry to pick one.

The harnesses predate this and each grew its own signature: some take the adapter
object (and call it in-process), others take its URL (the agent runs inside the
sandbox and dials back); one reports turns/submitted/format-errors, the rest
return a bare turn count. ``AgentTask`` carries the union of what any of them
needs so a rollouter builds it once, and ``AgentRun`` is what they all report
back -- which is what lets a run swap scaffolds from the environment instead of
from a code edit.

Adding a harness means writing ``async def my_agent(task: AgentTask) -> AgentRun``
and calling ``register_agent("my_agent", my_agent)``; no rollouter changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from torchtitan.experiments.rl.harness.adapters.anthropic import AnthropicAdapter
    from torchtitan.experiments.rl.harness.sandbox import Sandbox


@dataclass(frozen=True, kw_only=True, slots=True)
class AgentTask:
    """Everything any harness may need for one rollout."""

    sandbox: Sandbox
    """The task's live sandbox. Tools act here; grading reads this filesystem."""

    instruction: str
    """The task statement, rendered into whatever prompt the harness uses."""

    session_id: str
    """Adapter session this rollout's turns are captured under."""

    adapter: AnthropicAdapter
    """The policy. Host-side loops call ``adapter.complete`` in-process; agents that
    run inside the sandbox (or through an HTTP client) dial ``adapter.url``."""

    time_budget_sec: int
    """Wall-clock ceiling for the whole agent loop."""

    workdir: str = "/workspace"
    """Where the agent starts. Harnesses that navigate from the instruction ignore it."""

    max_turns: int | None = None
    """Turn cap; None keeps the harness default."""

    exec_timeout: int | None = None
    """Per-command timeout; None keeps the harness default."""

    pre_commands: list[str] | str | None = None
    """Setup commands some corpora ship (swe_r2e); None for the rest."""


@dataclass(frozen=True, kw_only=True, slots=True)
class AgentRun:
    """What a harness reports after its loop ends."""

    turns: int
    """Agent turns taken (>= 0)."""

    submitted: bool | None = None
    """Whether the agent signalled it was done.

    ``None`` means the harness has no submit signal at all, which is NOT the same
    as False. tmax grades only on submit (its verifier runs on the submit marker,
    matching the training env), so a caller must treat None as "grade anyway" --
    reading it as False would silently score every rollout 0.
    """

    format_errors: int = 0
    """Responses the harness could not parse into an action."""

    finish_reason: str = "unknown"
    """How the loop ended: submit / hit_max_turns / hit_time_budget /
    stopped_early / error, or "unknown" when the harness does not track it."""

    exec_trace: list[dict] = field(default_factory=list)
    """Every sandbox command the harness ran, in order, as
    ``{"t", "secs", "exit", "cmd"}`` (``cmd`` cut to 400 characters). A rollout's
    wall time is dominated by what happens between generations, and the training
    loop records only the total, so this is what tells a slow agent command from
    a slow harness. The rollouter writes it into the rollout record's ``exec``.
    Empty for harnesses that do not keep one."""

    pane_path: str | None = None
    """Where the harness's terminal transcript is INSIDE the sandbox, or None
    when it keeps none. Reported rather than fetched: only the rollouter knows
    whether this run collects transcripts and where they go, and the sandbox is
    still up when the harness returns, so it can read the file then."""


AgentFn = Callable[[AgentTask], Awaitable[AgentRun]]

_REGISTRY: dict[str, AgentFn] = {}


def register_agent(name: str, fn: AgentFn) -> None:
    """Register a harness under ``name``. Re-registering the same name is an error
    so two modules cannot silently fight over it."""
    if name in _REGISTRY and _REGISTRY[name] is not fn:
        raise ValueError(f"agent {name!r} is already registered")
    _REGISTRY[name] = fn


def get_agent(name: str) -> AgentFn:
    """Look up a harness by name, listing the alternatives when it is missing."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown agent {name!r}; registered: {sorted(_REGISTRY)}"
        ) from None


def registered_agents() -> list[str]:
    return sorted(_REGISTRY)
