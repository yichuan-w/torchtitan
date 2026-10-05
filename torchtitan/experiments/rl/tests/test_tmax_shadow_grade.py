# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Shadow grading of unsubmitted rollouts: measured, never scored.

tmax scores a rollout that ends on the context, time or turn limit 0 without running
the verifier; Harbor / TB-2.x grade every trial. ``shadow_grade_unsubmitted`` runs
the verifier on those rollouts so the record says how many of the zeros were real
solves, and must leave the reward -- and so the advantage -- exactly as before.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.examples.tmax import rollouter as rollouter_mod
from torchtitan.experiments.rl.examples.tmax.data import TMaxSample
from torchtitan.experiments.rl.examples.tmax.rollouter import (
    _RolloutIssueGate,
    _shadow_grade_metrics,
    TMaxRollouter,
)
from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn
from torchtitan.experiments.rl.harness.agents.spec import AgentRun
from torchtitan.experiments.rl.rollout.types import Rollout, RolloutStatus


def _rollouter(*, shadow: bool) -> TMaxRollouter:
    r = object.__new__(TMaxRollouter)
    r._agent_name = "vanillux"
    r._time_budget_sec = 3600
    r._eval_timeout_sec = 600
    r._max_context_tokens = 4096
    r._reward_mode = "sparse"
    r._read_ctrf = False
    r._shadow_grade_unsubmitted = shadow
    r._rollout_gate = _RolloutIssueGate(1)
    return r


def _run(rollouter, monkeypatch, *, submitted: bool, grade):
    @contextlib.asynccontextmanager
    async def fake_boot(*args, **kwargs):
        sandbox = AsyncMock()
        sandbox.sandbox_id = "sandbox-0"
        sandbox.disk_gb = 1
        yield sandbox

    async def fake_agent(task):
        return AgentRun(
            turns=1,
            submitted=submitted,
            finish_reason="submit" if submitted else "hit_context_limit",
        )

    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", fake_boot)
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: fake_agent)
    monkeypatch.setattr(rollouter_mod, "seed_workspace", AsyncMock())
    monkeypatch.setattr(rollouter_mod, "grade_tmax", grade)

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
    sample = TMaxSample(
        instance_id="task-1",
        image="example/image",
        workdir="/app",
        problem_statement="do the thing",
    )
    rollout, *_ = asyncio.run(
        rollouter._run_agent_rollout(
            adapter=adapter,
            generate_fn=AsyncMock(),
            sample=sample,
            group_id=0,
            rollout_idx=0,
            sampling=SimpleNamespace(seed=0),
            renderer=object(),
        )
    )
    return rollout


def _grade(value, calls):
    async def grade(sandbox, tmax, *, workdir, **kwargs):
        calls.append(kwargs)
        kwargs["diagnostics"]["exit_code"] = 0
        return value

    return grade


def test_an_unsubmitted_solve_is_recorded_but_still_scores_zero(monkeypatch):
    calls: list = []
    rollout = _run(
        _rollouter(shadow=True), monkeypatch, submitted=False, grade=_grade(1.0, calls)
    )
    assert len(calls) == 1
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 0.0}
    assert rollout.diagnostics["shadow_reward"] == 1.0
    assert rollout.diagnostics["verifier"]["shadow"] == {"reward": 1.0, "exit_code": 0}
    assert rollout.diagnostics["infra_failed"] is False


def test_off_by_default_nothing_extra_runs(monkeypatch):
    calls: list = []
    rollout = _run(
        _rollouter(shadow=False), monkeypatch, submitted=False, grade=_grade(1.0, calls)
    )
    assert calls == []
    assert rollout.diagnostics["shadow_reward"] is None
    assert "shadow" not in rollout.diagnostics["verifier"]


def test_a_submitted_rollout_is_graded_once_as_usual(monkeypatch):
    calls: list = []
    rollout = _run(
        _rollouter(shadow=True), monkeypatch, submitted=True, grade=_grade(1.0, calls)
    )
    assert len(calls) == 1
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}
    assert rollout.diagnostics["shadow_reward"] is None


def test_a_failed_shadow_grade_is_not_an_infra_failure(monkeypatch):
    async def broken(sandbox, tmax, *, workdir, **kwargs):
        raise RuntimeError("sandbox gone")

    rollout = _run(_rollouter(shadow=True), monkeypatch, submitted=False, grade=broken)
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 0.0}
    assert rollout.diagnostics["shadow_reward"] is None
    assert rollout.diagnostics["infra_failed"] is False


def _r(*, submitted, sparse=0.0, shadow=None, infra=False) -> Rollout:
    return Rollout(
        group_id=0,
        rollout_id=0,
        status=RolloutStatus.COMPLETED,
        diagnostics={
            "submitted": submitted,
            "sparse_reward": sparse,
            "shadow_reward": shadow,
            "infra_failed": infra,
        },
    )


def test_group_metrics():
    rollouts = [
        _r(submitted=True, sparse=1.0),
        _r(submitted=True, sparse=0.0),
        _r(submitted=False, shadow=1.0),
        _r(submitted=False, shadow=0.0),
        _r(submitted=False, shadow=None),  # shadow run failed: no verdict
        _r(submitted=False, infra=True),
    ]
    got = {
        metric.key: type(metric.value).reduce([metric.value])["mean"]
        for metric in _shadow_grade_metrics(rollouts)
    }
    assert got["rollout/shadow_unsubmitted_pass_frac"] == pytest.approx(0.5)
    # submitted 1, 0 + shadow 1, 0 -> 0.5; the failed shadow and the infra failure
    # are out of the denominator.
    assert got["rollout/shadow_reward_mean"] == pytest.approx(0.5)
