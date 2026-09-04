# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The grader's budget comes from the task, not from one value for the benchmark.

Harbor states ``[verifier].timeout_sec`` per task, the way it states the agent's.
The rollouter used to pass one configured ``TMAX_EVAL_TIMEOUT_SEC`` to every
grade call, so a task whose test suite is slower than that scored 0 for being
slow rather than for being wrong -- at the default 600s that is 87 of TB-2.1's
89 tasks, and its range (360s to 12000s) has no single value that is right.

The floor matters as much as the ceiling: 83 of those tasks run on a 1-vCPU
Daytona box, and the declared numbers were measured on the benchmark's own
runner, so a task declaring 360s must not be graded in 360s here just because it
said so. Configured value = floor, declared only ever raises it.

And the rollout's wall-clock guard has to be built from the SAME number: a guard
that assumes 600s while the grader runs on 12000s kills the rollout mid-grade
and reports an already-solved task as an infrastructure failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.examples.tmax import rollouter as rollouter_mod
from torchtitan.experiments.rl.examples.tmax.data import _parse_sample_row, TMaxSample
from torchtitan.experiments.rl.examples.tmax.rollouter import (
    _RolloutIssueGate,
    TMaxRollouter,
)
from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn
from torchtitan.experiments.rl.harness.agents.spec import AgentRun


def _rollouter(*, eval_timeout_sec: int = 600) -> TMaxRollouter:
    r = object.__new__(TMaxRollouter)
    r._agent_name = "vanillux"
    r._time_budget_sec = 3600
    r._eval_timeout_sec = eval_timeout_sec
    r._max_context_tokens = 4096
    r._reward_mode = "sparse"
    r._read_ctrf = False
    r._rollout_gate = _RolloutIssueGate(1)
    return r


def _sample(**kwargs) -> TMaxSample:
    return TMaxSample(
        instance_id="task-1",
        image="example/image",
        workdir="/app",
        problem_statement="do the thing",
        **kwargs,
    )


# --- the budget itself ---


def test_a_declared_budget_is_used():
    """build-pov-ray declares 12000s; at the old global 600s it could never pass."""
    r = _rollouter(eval_timeout_sec=600)
    assert r._verifier_budget_sec(_sample(verifier_timeout_sec=12000)) == 12000


def test_the_configured_value_is_a_floor_not_a_ceiling():
    """A task declaring less than the configured budget keeps the configured one:
    the declared number was measured on a runner faster than a 1-vCPU sandbox."""
    r = _rollouter(eval_timeout_sec=900)
    assert r._verifier_budget_sec(_sample(verifier_timeout_sec=360)) == 900


def test_a_corpus_that_declares_nothing_is_unchanged():
    """RTS and TerminalWorld rows carry no budget; they must keep the old behavior."""
    r = _rollouter(eval_timeout_sec=600)
    assert r._verifier_budget_sec(_sample()) == 600


def test_the_row_field_is_parsed():
    """Written by prepare_tb2_1_data.py, and dropped on the floor until now."""
    row = {
        "metadata": {
            "instance_id": "build-pov-ray",
            "image": "example/image",
            "verifier_timeout_sec": 12000.0,
            "agent_timeout_sec": 12000.0,
            "tmax": {"test_sh": "true"},
        }
    }
    assert _parse_sample_row(row).verifier_timeout_sec == 12000.0
    del row["metadata"]["verifier_timeout_sec"]
    assert _parse_sample_row(row).verifier_timeout_sec is None


# --- the guard built from it ---


def test_the_guard_covers_the_declared_verifier():
    """Same number in both places, or the rollout dies while the grader is running."""
    r = _rollouter(eval_timeout_sec=600)
    assert r._guard_for(3600, 12000) == 3600 + 12000 + 300
    # Omitted -> the configured default, which is what the boot-time fallback uses.
    assert r._guard_for(3600) == 3600 + 600 + 300


# --- end to end through a rollout ---


def _run_rollout(rollouter: TMaxRollouter, sample: TMaxSample, monkeypatch):
    """Drive one rollout with the sandbox, agent and grader stubbed, and report
    what the grade call and the wall-clock guard were actually given."""
    seen: dict = {}

    @contextlib.asynccontextmanager
    async def fake_boot(*args, **kwargs):
        yield AsyncMock()

    async def fake_agent(task):
        return AgentRun(turns=1, submitted=True, finish_reason="submit")

    async def fake_grade(sandbox, tmax, *, workdir, timeout_sec=None):
        seen["timeout_sec"] = timeout_sec
        return 1.0

    real_timeout = asyncio.timeout

    def spy_timeout(delay):
        seen.setdefault("guard", delay)
        return real_timeout(delay)

    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", fake_boot)
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: fake_agent)
    monkeypatch.setattr(rollouter_mod, "seed_workspace", AsyncMock())
    monkeypatch.setattr(rollouter_mod, "grade_tmax", fake_grade)
    monkeypatch.setattr(rollouter_mod.asyncio, "timeout", spy_timeout)

    adapter = AsyncMock()
    adapter.open_session = lambda *a, **k: None
    adapter.finish_session = AsyncMock(
        return_value=[
            CapturedTurn(
                prompt_token_ids=[1, 2],
                completion_token_ids=[3],
                completion_logprobs=[-0.1],
                min_policy_version=0,
                max_policy_version=0,
                finish_reason="stop",
                extends_previous=False,
            )
        ]
    )
    rollout, *_ = asyncio.run(
        rollouter._run_agent_rollout(
            adapter=adapter,
            generate_fn=AsyncMock(),
            sample=sample,
            group_id=0,
            rollout_idx=0,
            sampling=object(),
            renderer=object(),
        )
    )
    return rollout, seen


def test_the_grade_call_gets_the_tasks_own_budget(monkeypatch):
    rollouter = _rollouter(eval_timeout_sec=600)
    sample = _sample(verifier_timeout_sec=7200, agent_timeout_sec=7200)
    rollout, seen = _run_rollout(rollouter, sample, monkeypatch)

    assert seen["timeout_sec"] == 7200
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}
    # The guard is the agent budget + THAT verifier budget + boot, plus the
    # sandbox-queue allowance the rollout starts with.
    agent_budget = rollouter._agent_budget_sec(sample)
    assert seen["guard"] == pytest.approx(
        agent_budget + 7200 + 300 + rollouter._SANDBOX_BOOT_ALLOWANCE_SEC
    )


def test_a_row_with_no_declared_budget_still_grades(monkeypatch):
    rollouter = _rollouter(eval_timeout_sec=600)
    _, seen = _run_rollout(rollouter, _sample(), monkeypatch)
    assert seen["timeout_sec"] == 600


def test_the_real_tb2_1_range_is_covered():
    """The 89 declared budgets a TB-2.1 pass actually grades on."""
    r = _rollouter(eval_timeout_sec=600)
    declared = [360.0, 900.0, 1800.0, 3600.0, 7200.0, 12000.0]
    assert [
        r._verifier_budget_sec(_sample(verifier_timeout_sec=d)) for d in declared
    ] == [
        600,
        900,
        1800,
        3600,
        7200,
        12000,
    ]
    assert json.dumps(declared)  # the values are plain JSON numbers in the row
