# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""A sandbox the agent destroyed is scored 0; one the platform lost stays masked."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.actors.generator import SamplingConfig
from torchtitan.experiments.rl.examples.tmax import rollouter as rollouter_mod
from torchtitan.experiments.rl.examples.tmax.data import TMaxSample
from torchtitan.experiments.rl.examples.tmax.rollouter import _FINISH_REASONS
from torchtitan.experiments.rl.examples.tmax.self_destruct import (
    last_turn_commands,
    match_self_destruct,
)
from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn
from torchtitan.experiments.rl.harness.agents.spec import AgentRun
from torchtitan.experiments.rl.harness.agents.terminus import _SandboxExecutionError
from torchtitan.experiments.rl.harness.agents.terminus_terminal import (
    TerminalUnavailable,
)
from torchtitan.experiments.rl.harness.sandbox.base import SandboxIssue
from torchtitan.experiments.rl.rollout import RolloutStatus
from torchtitan.experiments.rl.tests.test_tmax_ctrf import (
    _stub_rollouter as _ctrf_stub_rollouter,
)


def _stub_rollouter(monkeypatch, **kwargs):
    """test_tmax_ctrf's stub plus what the rollout gained since it was written."""
    rollouter = _ctrf_stub_rollouter(monkeypatch, **kwargs)
    rollouter._eval_agent_budget_cap_sec = 0
    rollouter._eval_verifier_budget_cap_sec = 0
    monkeypatch.setattr(rollouter_mod, "_net_probe", AsyncMock(return_value={}))
    monkeypatch.setattr(rollouter_mod, "capture_baseline", AsyncMock(return_value=None))
    monkeypatch.setattr(rollouter_mod, "_set_debconf_noninteractive", AsyncMock())
    return rollouter


@pytest.mark.parametrize(
    "command",
    [
        "tmux kill-server",
        "pkill tmux",
        "pkill -9 -f daytona",
        "killall -9 tmux",
        "kill -9 $(pgrep tmux)",
        "kill -9 -1",
        "kill -KILL -1",
        "kill -s KILL -1",
        "kill -- -1",
        "kill 1",
        "kill -9 1",
        "kill -9 $$",
        "kill $PPID",
        "pkill bash",
        "killall -u root",
        "apt-get remove -y tmux",
        "apt purge tmux",
        "DEBIAN_FRONTEND=x; sudo apt-get -y autoremove tmux",
        "dpkg -r tmux",
        "rm -rf / --no-preserve-root",
        "reboot",
        "sudo shutdown -h now",
        "cd /app && make clean; pkill -f daytona; ls",
        "timeout 5 pkill tmux",
        "tmux kill-session",
        "tmux kill-session -t terminus-2",
        "pkill -P 1",
        "pkill server",
        "pkill -f login",
        "pkill -9 bash",
        "ps aux | grep daytona | awk '{print $2}' | xargs kill -9",
        # Eval run r1026bs300 (2026-10-27): sandboxes lost after these.
        "python3 /app/final_test.py &\nsleep 2\nkill -INT -1\nwait",
        "kill -9 $(ps aux | awk '{print $2}')",
        'pkill -9 -f "dashboard\\|terminal\\|toolbox\\|computer\\|record"',
        "find / -type f -newer /app/main.db -delete 2>/dev/null",
    ],
)
def test_self_destruct_commands_match(command: str) -> None:
    assert match_self_destruct([command]) is not None


@pytest.mark.parametrize(
    "command",
    [
        "pkill -f myserver.py",
        "pkill -f 'python -m http.server'",
        "kill %1",
        "kill -9 1234",
        "kill -1 1234",
        "kill $(cat /tmp/server.pid)",
        "killall node",
        "tmux new-session -d -s srv",
        "echo 'pkill tmux' > notes.txt",
        "grep reboot /var/log/syslog",
        "apt-get install -y tmux",
        "apt-get remove -y nginx",
        "rm -rf /tmp/build",
        "rm -rf /app/*",
        "man shutdown",
        "python3 -c 'import os'",
        "tmux kill-session -t main",
        "pkill sleep",
        "rm -f /usr/lib/x86_64-linux-gnu/libz.so.1",
        "cat > bomb.sh << 'EOF'\n:(){ :|:& };:\npkill tmux\nEOF\n",
        "halt:\n  jmp halt",
        "kill -9 -1234",
        "kill -9 $(ps aux | grep python | awk '{print $2}')",
        "find / -name '*.pyc' -delete",
        "find /tmp -type f -delete",
    ],
)
def test_ordinary_commands_do_not_match(command: str) -> None:
    assert match_self_destruct([command]) is None


def _turn(content) -> CapturedTurn:
    return CapturedTurn(
        prompt_token_ids=[1, 2],
        completion_token_ids=[3],
        completion_logprobs=[-0.1],
        min_policy_version=0,
        max_policy_version=0,
        finish_reason="stop",
        extends_previous=False,
        completion_message={"role": "assistant", "content": content},
    )


_TERMINUS_REPLY = (
    "<response><analysis>The server is stuck.</analysis><plan>Kill it.</plan>"
    '<commands><keystrokes duration="1.0">ls /app\n</keystrokes>'
    '<keystrokes duration="1.0">pkill -9 -f daytona &amp;&amp; echo done\n'
    "</keystrokes></commands></response>"
)


def test_last_turn_commands_reads_terminus_keystrokes() -> None:
    captured = [
        _turn([{"type": "text", "text": "<response><commands>"
                '<keystrokes duration="1.0">tmux kill-server\n</keystrokes>'
                "</commands></response>"}]),
        _turn([{"type": "thinking", "thinking": "pkill tmux?"},
               {"type": "text", "text": _TERMINUS_REPLY}]),
    ]
    assert last_turn_commands(captured, num_turns=1) == [
        "ls /app\n",
        "pkill -9 -f daytona &amp;&amp; echo done\n",
    ]
    assert match_self_destruct(last_turn_commands(captured, num_turns=1)) == (
        "kill_daytona: pkill -9 -f daytona && echo done"
    )
    # The turn before the last is checked too: the kill there is often caught
    # only at the next turn's first keystroke.
    assert last_turn_commands(captured)[0] == "tmux kill-server\n"


def test_last_turn_commands_reads_tool_calls() -> None:
    assert last_turn_commands(
        [_turn([{"type": "tool_use", "input": {"command": "kill -9 -1"}}])]
    ) == ["kill -9 -1"]
    assert last_turn_commands(
        [_turn([{"type": "text",
                 "text": "<tool_call>\n<parameter=command>\nreboot\n</parameter>"}])]
    ) == ["reboot"]
    assert last_turn_commands([]) == []


def _run(rollouter, captured, *, group_id: int = 0):
    adapter = AsyncMock()
    adapter.open_session = lambda *a, **k: None
    adapter.finish_session = AsyncMock(return_value=captured)
    return asyncio.run(
        rollouter._run_agent_rollout(
            adapter=adapter,
            generate_fn=AsyncMock(),
            sample=TMaxSample(
                instance_id="task-1",
                image="example/image",
                workdir="/app",
                problem_statement="do the thing",
            ),
            group_id=group_id,
            rollout_idx=0,
            sampling=SamplingConfig(),
            renderer=object(),
        )
    )


def _agent_raising(monkeypatch, error: Exception) -> None:
    monkeypatch.setattr(
        rollouter_mod, "get_agent", lambda name: AsyncMock(side_effect=error)
    )


@pytest.mark.parametrize("group_id", [0, -1])
def test_destroyed_sandbox_is_scored_zero(monkeypatch, group_id: int) -> None:
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=None)
    _agent_raising(
        monkeypatch, TerminalUnavailable("terminal_state_unavailable", [])
    )
    rollout, submitted, _, reason, diagnostics = _run(
        rollouter, [_turn([{"type": "text", "text": _TERMINUS_REPLY}])],
        group_id=group_id,
    )
    assert reason == "sandbox_destroyed" and reason in _FINISH_REASONS
    assert rollout.status == RolloutStatus.COMPLETED
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 0.0}
    assert diagnostics.infra_failed is False
    assert diagnostics.failure == {
        "origin": "agent",
        "reason": "sandbox_destroyed",
        "stage": "agent",
        "matched": "kill_daytona: pkill -9 -f daytona && echo done",
    }
    assert not submitted
    grade = rollouter_mod.grade_tmax
    assert isinstance(grade, AsyncMock)
    grade.assert_not_awaited()


def test_lost_sandbox_with_ordinary_last_command_stays_masked(monkeypatch) -> None:
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=None)
    _agent_raising(monkeypatch, _SandboxExecutionError("Failed to create session:"))
    reply = '<response><commands><keystrokes>ls\n</keystrokes></commands></response>'
    rollout, _, _, reason, diagnostics = _run(
        rollouter, [_turn([{"type": "text", "text": reply}])]
    )
    assert rollout.status == RolloutStatus.ERROR
    assert diagnostics.infra_failed is True and reason == "error"


def test_self_destruct_command_without_a_failure_is_not_rescored(monkeypatch) -> None:
    """The command alone proves nothing: a rollout that went on normally keeps
    its graded outcome."""
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=None)
    rollout, _, _, reason, diagnostics = _run(
        rollouter, [_turn([{"type": "text", "text": _TERMINUS_REPLY}])]
    )
    assert reason == "submit" and diagnostics.infra_failed is False
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}


def test_timeout_keeps_the_mask(monkeypatch) -> None:
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=None)
    _agent_raising(monkeypatch, TimeoutError())
    rollout, _, _, _, diagnostics = _run(
        rollouter, [_turn([{"type": "text", "text": _TERMINUS_REPLY}])]
    )
    assert rollout.status == RolloutStatus.ERROR_TIMEOUT
    assert diagnostics.infra_failed is True


def test_kill_switch_restores_the_mask(monkeypatch) -> None:
    monkeypatch.setattr(rollouter_mod, "_SCORE_SELF_DESTRUCT", False)
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=None)
    _agent_raising(
        monkeypatch, TerminalUnavailable("terminal_state_unavailable", [])
    )
    _, _, _, _, diagnostics = _run(
        rollouter, [_turn([{"type": "text", "text": _TERMINUS_REPLY}])]
    )
    assert diagnostics.infra_failed is True


def _boot_with_tracker(monkeypatch, trackers: list) -> None:
    @contextlib.asynccontextmanager
    async def boot(*args, issue_tracker=None, **kwargs):
        trackers.append(issue_tracker)
        sandbox = AsyncMock()
        sandbox.sandbox_id = "sb"
        sandbox.allocated_disk_gb = 1
        yield sandbox

    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", boot)


def _disk_issue() -> SandboxIssue:
    return SandboxIssue(
        provider="daytona",
        kind="command_disk_exhausted",
        phase="command",
        recovered=False,
        error_type="",
        message="mkdir: cannot create directory '/tests': No space left on device",
    )


def test_disk_full_at_verifier_upload_is_scored_zero(monkeypatch) -> None:
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=None)
    trackers: list = []
    _boot_with_tracker(monkeypatch, trackers)

    async def grade_on_full_disk(*args, **kwargs):
        trackers[0].record(_disk_issue())
        raise RuntimeError("upload /tests/test.sh: no space left on device")

    monkeypatch.setattr(rollouter_mod, "grade_tmax", grade_on_full_disk)
    rollout, _, _, reason, diagnostics = _run(rollouter, [_turn([])])
    assert reason == "disk_exhausted"
    assert rollout.status == RolloutStatus.COMPLETED
    assert diagnostics.infra_failed is False
    assert diagnostics.failure["stage"] == "verifier"


def test_verifier_failure_without_a_new_disk_event_stays_masked(monkeypatch) -> None:
    """A disk event from the agent's run does not blame an unrelated verifier
    failure on the agent."""
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=None)
    trackers: list = []
    _boot_with_tracker(monkeypatch, trackers)

    async def agent(task):
        trackers[0].record(_disk_issue())
        return AgentRun(turns=1, submitted=True, finish_reason="submit")

    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: agent)
    monkeypatch.setattr(
        rollouter_mod,
        "grade_tmax",
        AsyncMock(side_effect=RuntimeError("pre-grade setup timed out")),
    )
    _, _, _, _, diagnostics = _run(rollouter, [_turn([])])
    assert diagnostics.infra_failed is True
