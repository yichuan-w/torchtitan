# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Pluggable coding-agent harness for TorchTitan RL.

An external CLI agent (Claude Code first) runs unmodified inside a cloud sandbox
and is pointed at an on-box wire-format adapter that serves the trained policy and
captures every turn as on-policy training tokens. Three orthogonal axes, each its
own subpackage:

  - ``sandbox``: WHERE code runs -- provider-agnostic ``Sandbox`` contract +
    the Daytona backend + the Daytona fs-relay ``bridge``.
  - ``adapters``: HOW the model is served to the agent -- a token-capturing HTTP
    endpoint per wire format (``anthropic`` for Claude Code; add ``openai`` for
    Codex/OpenCode).
  - ``agents``: WHICH CLI agent + how to launch it (``claude_code``).

Adding a new CLI agent = a new ``agents`` runner (+ reuse/extend an ``adapters``
wire module); a new sandbox provider = a new ``sandbox`` backend. R2E (SWE) task
data + grading live in ``examples/swe_r2e``.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from torchtitan.experiments.rl.harness.adapters import AnthropicAdapter, CapturedTurn
from torchtitan.experiments.rl.harness.agents import (
    apply_pre_commands,
    boot_agent_sandbox,
    git_diff,
    run_claude_code,
    run_host_loop,
)
from torchtitan.experiments.rl.harness.agents.spec import (
    AgentFn,
    AgentRun,
    AgentTask,
    get_agent,
    register_agent,
    registered_agents,
)
from torchtitan.experiments.rl.harness.sandbox import (
    DaytonaSandbox,
    make_sandbox,
    Sandbox,
    SandboxIssue,
    SandboxIssueTracker,
    SandboxLogContext,
)


def __getattr__(name: str):
    # Terminal replay uses a supplied policy and needs no training adapter.
    if name in ("AnthropicAdapter", "CapturedTurn"):
        from torchtitan.experiments.rl.harness import adapters

        value = getattr(adapters, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AgentFn",
    "AgentRun",
    "AgentTask",
    "AnthropicAdapter",
    "CapturedTurn",
    "DaytonaSandbox",
    "Sandbox",
    "SandboxIssue",
    "SandboxIssueTracker",
    "SandboxLogContext",
    "apply_pre_commands",
    "boot_agent_sandbox",
    "get_agent",
    "git_diff",
    "make_sandbox",
    "register_agent",
    "registered_agents",
    "run_claude_code",
    "run_host_loop",
]
