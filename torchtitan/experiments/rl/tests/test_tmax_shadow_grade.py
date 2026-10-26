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
    _drop_context_wall_turn,
    _strict_reward_metric,
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
    r._eval_max_context_tokens = 4096
    r._eval_max_turns = 0
    r._eval_agent_budget_cap_sec = 0
    r._eval_verifier_budget_cap_sec = 0
    r._strict_format = r._strict_opening = r._strict_truncation = False
    r._eval_strict_format = r._eval_strict_opening = r._eval_strict_truncation = False
    r._reward_mode = "sparse"
    r._read_ctrf = False
    r._shadow_grade_unsubmitted = shadow
    r._eval_shadow_grade_timeouts = True
    r._eval_grade_unsubmitted = False
    r._grade_unsubmitted = False
    r._eval_context_notice = True
    r._format_streak_limit = 0
    r._rollout_gate = _RolloutIssueGate(1)
    return r


def _run(
    rollouter,
    monkeypatch,
    *,
    submitted: bool,
    grade,
    tmax=None,
    boot=None,
    finish_reason="hit_context_limit",
    group_id=0,
    agent=None,
):
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
            finish_reason="submit" if submitted else finish_reason,
        )

    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", boot or fake_boot)
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: agent or fake_agent)
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
        **({"tmax": tmax} if tmax is not None else {}),
    )
    rollout, *_ = asyncio.run(
        rollouter._run_agent_rollout(
            adapter=adapter,
            generate_fn=AsyncMock(),
            sample=sample,
            group_id=group_id,
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


@pytest.mark.parametrize(
    "group_id, finish_reason, eval_timeouts, graded",
    [
        (-1, "hit_time_budget", False, False),
        (-1, "hit_time_budget", True, True),
        (-1, "hit_context_limit", False, True),
        (0, "hit_time_budget", False, True),
    ],
)
def test_validation_timeouts_skip_the_shadow_grade_when_off(
    monkeypatch, group_id, finish_reason, eval_timeouts, graded
):
    rollouter = _rollouter(shadow=True)
    rollouter._eval_shadow_grade_timeouts = eval_timeouts
    rollouter._eval_grade_unsubmitted = False
    rollouter._grade_unsubmitted = False
    rollouter._eval_context_notice = True
    rollouter._format_streak_limit = 0
    calls = []
    _run(
        rollouter,
        monkeypatch,
        submitted=False,
        grade=_grade(1.0, calls),
        finish_reason=finish_reason,
        group_id=group_id,
    )
    assert bool(calls) == graded


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
    # submitted 1, 0 + shadow 1, 0 + no verdict 0 -> 0.4; only the infra failure
    # is out of the denominator.
    assert got["rollout/shadow_reward_mean"] == pytest.approx(0.4)


@pytest.mark.parametrize(
    "group_id,finish_reason,reward,graded",
    [
        (-1, "hit_time_budget", 1.0, True),
        (-1, "hit_context_limit", 1.0, True),
        (-1, "hit_max_turns", 1.0, True),
        (-1, "format_error", 0.0, False),  # rule-terminated: still 0, ungraded
        (0, "hit_time_budget", 0.0, False),  # training is unchanged
    ],
)
def test_validation_scores_unsubmitted_at_the_verdict(
    monkeypatch, group_id, finish_reason, reward, graded
):
    rollouter = _rollouter(shadow=False)
    rollouter._eval_shadow_grade_timeouts = False
    rollouter._eval_grade_unsubmitted = True
    calls: list = []
    rollout = _run(
        rollouter,
        monkeypatch,
        submitted=False,
        grade=_grade(1.0, calls),
        finish_reason=finish_reason,
        group_id=group_id,
    )
    assert len(calls) == (1 if graded else 0)
    assert rollout.turns[-1].env_rewards == {"tmax_reward": reward}
    assert rollout.diagnostics["submitted"] is False
    assert rollout.diagnostics["shadow_reward"] == (1.0 if graded else None)



def test_a_separate_verifier_grades_in_its_own_sandbox_from_the_artifacts(monkeypatch):
    """Harbor environment_mode="separate": the submitted rollout's artifacts are
    copied into a sandbox of the verifier image and graded there, with no
    upload of the tests the image already holds, and no shadow grading."""
    booted: list = []
    moved: list = []
    calls: list = []

    @contextlib.asynccontextmanager
    async def boot(image, **kwargs):
        sandbox = AsyncMock()
        sandbox.sandbox_id = f"sb-{len(booted)}"
        sandbox.disk_gb = 1
        booted.append(image)
        yield sandbox

    async def collect(sandbox, paths):
        moved.append(("collect", sandbox.sandbox_id, paths))
        return b"tar"

    async def restore(sandbox, data):
        moved.append(("restore", sandbox.sandbox_id, data))

    async def grade(sandbox, tmax, *, workdir, **kwargs):
        calls.append((sandbox.sandbox_id, kwargs.get("upload_tests", True)))
        return 1.0

    from torchtitan.experiments.rl.examples.tmax import grading as grading_mod

    monkeypatch.setattr(grading_mod, "collect_artifacts", collect)
    monkeypatch.setattr(grading_mod, "restore_artifacts", restore)
    monkeypatch.setattr(grading_mod, "grade_tmax", grade)
    tmax = {
        "test_sh": "",
        "separate_verifier": {"image": "verifier/image", "artifacts": ["/app/out.step"]},
    }
    rollout = _run(
        _rollouter(shadow=True), monkeypatch, submitted=True, grade=grade,
        tmax=tmax, boot=boot,
    )

    assert booted == ["example/image", "verifier/image"]
    assert moved == [
        ("collect", "sb-0", ["/app/out.step"]),
        ("restore", "sb-1", b"tar"),
    ]
    assert calls == [("sb-1", False)]
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}
    assert rollout.diagnostics["infra_failed"] is False


def test_strict_reward_counts_only_submitted_verdicts():
    rollouts = [
        _r(submitted=True, sparse=1.0),
        _r(submitted=False, sparse=1.0, shadow=1.0),  # graded unsubmitted
        _r(submitted=False, infra=True),
        _r(submitted=True, sparse=0.0),
    ]
    metric = _strict_reward_metric(rollouts)
    assert metric.key == "rollout/strict_reward_mean"
    assert type(metric.value).reduce([metric.value])["mean"] == pytest.approx(0.25)


def test_training_scores_unsubmitted_at_the_verdict_when_on(monkeypatch):
    rollouter = _rollouter(shadow=False)
    rollouter._grade_unsubmitted = True
    calls: list = []
    rollout = _run(
        rollouter,
        monkeypatch,
        submitted=False,
        grade=_grade(1.0, calls),
        finish_reason="hit_max_turns",
        group_id=3,
    )
    assert len(calls) == 1
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}
    assert rollout.diagnostics["shadow_reward"] == 1.0


def _ct(finish_reason: str, tokens=(3,)) -> CapturedTurn:
    return CapturedTurn(
        prompt_token_ids=[1, 2],
        completion_token_ids=list(tokens),
        completion_logprobs=[-0.1] * len(tokens),
        min_policy_version=0,
        max_policy_version=0,
        finish_reason=finish_reason,
        extends_previous=False,
    )


@pytest.mark.parametrize(
    "captured,kept",
    [
        ([_ct("stop"), _ct("length")], 1),  # wall-truncated last turn dropped
        ([_ct("stop"), _ct("length"), _ct("length", ())], 1),  # empty tail skipped
        ([_ct("stop"), _ct("stop")], 2),  # last turn complete: kept
        ([_ct("length")], 1),  # never drops the only turn
    ],
)
def test_drop_context_wall_turn(captured, kept):
    turns = [object() for ct in captured if ct.completion_token_ids]
    _drop_context_wall_turn(turns, captured)
    assert len(turns) == kept



@pytest.mark.parametrize(
    "group_id,eval_notice,shown",
    [(-1, False, False), (-1, True, True), (0, False, True)],
)
def test_eval_context_notice_reaches_the_agent(
    monkeypatch, group_id, eval_notice, shown
):
    rollouter = _rollouter(shadow=False)
    rollouter._eval_context_notice = eval_notice
    seen: list = []

    async def agent(task):
        seen.append(task.context_notice)
        return AgentRun(turns=1, submitted=True, finish_reason="submit")

    _run(
        rollouter,
        monkeypatch,
        submitted=True,
        grade=_grade(1.0, []),
        group_id=group_id,
        agent=agent,
    )
    assert seen == [shown]


@pytest.mark.parametrize(
    "readout_eval_budget, group_id, max_turns, budget",
    [
        (False, -1, 60, 0),
        (True, -1, None, 3600),
        (True, 0, 60, 0),
    ],
)
def test_bench_readout_eval_budget_keeps_the_eval_budget_for_validation_only(
    monkeypatch, readout_eval_budget, group_id, max_turns, budget
):
    rollouter = _rollouter(shadow=False)
    rollouter._bench = rollouter_mod._BenchMode(
        max_turns=60, readout_eval_budget=readout_eval_budget
    )
    seen = []

    async def recording_agent(task):
        seen.append(task)
        return AgentRun(turns=1, submitted=True, finish_reason="submit")

    monkeypatch.setattr(rollouter, "_agent_budget_sec", lambda sample: 3600)
    _run(
        rollouter,
        monkeypatch,
        submitted=True,
        grade=_grade(1.0, []),
        group_id=group_id,
        agent=recording_agent,
    )
    assert seen and seen[0].max_turns == max_turns
    assert seen[0].time_budget_sec == budget


@pytest.mark.parametrize(
    "train_budget_sec, group_id, max_turns, budget",
    [
        (7200, 0, 120, 7200),
        (0, 0, 120, 0),
        (7200, -1, None, 3600),
    ],
)
def test_bench_train_budget_clocks_training_attempts_only(
    monkeypatch, train_budget_sec, group_id, max_turns, budget
):
    rollouter = _rollouter(shadow=False)
    rollouter._bench = rollouter_mod._BenchMode(
        max_turns=120, train_budget_sec=train_budget_sec, readout_eval_budget=True
    )
    seen = []

    async def recording_agent(task):
        seen.append(task)
        return AgentRun(turns=1, submitted=True, finish_reason="submit")

    monkeypatch.setattr(rollouter, "_agent_budget_sec", lambda sample: 3600)
    _run(
        rollouter,
        monkeypatch,
        submitted=True,
        grade=_grade(1.0, []),
        group_id=group_id,
        agent=recording_agent,
    )
    assert seen and seen[0].max_turns == max_turns
    assert seen[0].time_budget_sec == budget
