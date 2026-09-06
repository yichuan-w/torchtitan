# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The verifier's second output: per-test CTRF parsing + its group metrics.

``_REAL_CTRF`` is verbatim output from ``pytest-json-ctrf==0.3.5`` (the pin every
tmax/RTS ``test.sh`` uses) on a 2-passed/1-failed file, so the parser is tested
against the real schema rather than a hand-written guess.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.examples.tmax import rollouter as rollouter_mod
from torchtitan.experiments.rl.examples.tmax.data import TMaxSample
from torchtitan.experiments.rl.examples.tmax.grading import (
    ctrf_pass_fraction,
    parse_ctrf,
)
from torchtitan.experiments.rl.examples.tmax.rollouter import (
    _ctrf_metrics,
    _RolloutIssueGate,
    TMaxRollouter,
)
from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn
from torchtitan.experiments.rl.harness.agents.spec import AgentRun
from torchtitan.experiments.rl.observability import metrics as m
from torchtitan.experiments.rl.rollout import RolloutStatus

_REAL_CTRF = """{
  "results": {
    "tool": {"name": "pytest", "version": "8.4.1"},
    "summary": {"tests": 3, "passed": 2, "failed": 1, "skipped": 0,
                "pending": 0, "other": 0,
                "start": 1786433705.6701994, "stop": 1786433705.7090254},
    "tests": [
      {"name": "test_probe.py::test_check_01_required_evidence",
       "status": "passed", "duration": 0.0005, "retries": 0},
      {"name": "test_probe.py::test_check_04_no_shortcut",
       "status": "failed", "raw_status": "call_failed", "duration": 0.0004,
       "message": "The test failed in the call phase due to an assertion error",
       "trace": "assert 0"},
      {"name": "test_probe.py::test_check_03_final_semantics",
       "status": "passed", "duration": 0.0003, "retries": 0}
    ]
  }
}"""


def _reduced(reports: list[dict | None]) -> dict[str, float]:
    """Metric key -> the mean the aggregator would report (Mean holds a sum+count)."""
    return {
        metric.key: m.Mean.reduce([metric.value])["mean"]
        for metric in _ctrf_metrics(reports)
    }


def test_parses_real_ctrf_schema() -> None:
    report = parse_ctrf(_REAL_CTRF)
    assert report == {
        "tests": 3,
        "passed": 2,
        "failed": ["test_probe.py::test_check_04_no_shortcut"],
    }


def test_skipped_tests_are_not_reported_as_failed() -> None:
    raw = json.loads(_REAL_CTRF)
    raw["results"]["tests"][1]["status"] = "skipped"
    raw["results"]["summary"] = {"tests": 3, "passed": 2, "skipped": 1}
    assert parse_ctrf(json.dumps(raw))["failed"] == []


@pytest.mark.parametrize(
    "text",
    [
        "",  # the ~6% of tasks whose verifier writes no ctrf at all
        "not json",
        "{}",  # no "results"
        '{"results": {"tests": []}}',  # no "summary"
        '{"results": {"summary": {"passed": 1}}}',  # no total
    ],
)
def test_absent_or_malformed_report_is_none(text: str) -> None:
    assert parse_ctrf(text) is None


def test_group_metrics_split_pass_rate_from_failing_check() -> None:
    reports = [
        {"tests": 4, "passed": 4, "failed": []},
        {"tests": 4, "passed": 3, "failed": ["t::test_check_04_no_shortcut"]},
        None,  # sibling that never submitted, or wrote no report
    ]
    values = _reduced(reports)
    assert values["rollout/ctrf_report_frac"] == pytest.approx(2 / 3)
    # Mean over the siblings that reported: (4/4 + 3/4) / 2.
    assert values["rollout/ctrf_test_pass_frac"] == pytest.approx(0.875)
    assert values["rollout/ctrf_check_no_shortcut_fail_frac"] == pytest.approx(0.5)
    assert values["rollout/ctrf_check_final_semantics_fail_frac"] == pytest.approx(0.0)


def test_group_metrics_without_any_report_are_nan() -> None:
    """No sibling reported -> empty means, which the metrics aggregator drops."""
    values = _reduced([None, None])
    assert values["rollout/ctrf_report_frac"] == 0.0
    assert math.isnan(values["rollout/ctrf_test_pass_frac"])
    assert math.isnan(values["rollout/ctrf_check_no_shortcut_fail_frac"])


def _stub_rollouter(
    monkeypatch,
    *,
    ctrf_result,
    reward_mode: str = "sparse",
    sparse_reward: float = 1.0,
) -> TMaxRollouter:
    """A rollouter whose sandbox/agent/grading are stubs, so only the CTRF read and
    the reward mode vary. ``ctrf_result`` is a report or an exception to raise."""
    rollouter = object.__new__(TMaxRollouter)
    rollouter._agent_name = "vanillux"
    rollouter._time_budget_sec = 60
    rollouter._eval_timeout_sec = 60
    rollouter._guard_sec = 300
    rollouter._max_context_tokens = 4096
    rollouter._reward_mode = reward_mode
    rollouter._read_ctrf = True
    rollouter._rollout_gate = _RolloutIssueGate(1)

    @contextlib.asynccontextmanager
    async def fake_boot(*args, **kwargs):
        yield AsyncMock()

    async def fake_agent(task):
        return AgentRun(turns=1, submitted=True, finish_reason="submit")

    async def fake_read_ctrf(sandbox, **kwargs):
        if isinstance(ctrf_result, Exception):
            raise ctrf_result
        return ctrf_result

    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", fake_boot)
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: fake_agent)
    monkeypatch.setattr(rollouter_mod, "seed_workspace", AsyncMock())
    monkeypatch.setattr(
        rollouter_mod, "grade_tmax", AsyncMock(return_value=sparse_reward)
    )
    monkeypatch.setattr(rollouter_mod, "read_ctrf_report", fake_read_ctrf)
    return rollouter


def _run_rollout(rollouter: TMaxRollouter, *, group_id: int = 0):
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
            sampling=object(),
            renderer=object(),
        )
    )


def test_failing_ctrf_read_cannot_change_reward_or_status(monkeypatch) -> None:
    """The graded reward must survive a diagnostics read that raises.

    `sandbox.exec` raises when the sandbox is gone or the Daytona API errors, and
    an unguarded read would land in the rollout's `except Exception` handler --
    turning an already-graded reward=1.0 into reward=0 + infra_failed.
    """
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=RuntimeError("sandbox lost"))
    rollout, submitted, _, finish_reason, diagnostics = _run_rollout(rollouter)

    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}
    assert rollout.status == RolloutStatus.COMPLETED
    assert submitted is True
    assert finish_reason == "submit"
    assert diagnostics.infra_failed is False
    assert rollout.diagnostics["ctrf"] is None


def test_sandbox_execution_error_marks_rollout_unscored(monkeypatch) -> None:
    from torchtitan.experiments.rl.harness.agents.terminus import _SandboxExecutionError

    rollouter = _stub_rollouter(monkeypatch, ctrf_result=None)
    agent = AsyncMock(side_effect=_SandboxExecutionError("Failed to create session:"))
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: agent)
    rollout, _, _, _, diagnostics = _run_rollout(rollouter)
    assert rollout.status == RolloutStatus.ERROR
    assert diagnostics.infra_failed is True
    rollouter_mod.grade_tmax.assert_not_awaited()


def test_successful_ctrf_read_is_recorded(monkeypatch) -> None:
    report = {"tests": 4, "passed": 3, "failed": ["t::test_check_04_no_shortcut"]}
    rollouter = _stub_rollouter(monkeypatch, ctrf_result=report)
    rollout, _, _, _, _ = _run_rollout(rollouter)

    assert rollout.diagnostics["ctrf"] == report
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}


# --- dense reward mode ---


def test_dense_pass_fraction() -> None:
    assert ctrf_pass_fraction({"tests": 4, "passed": 3, "failed": []}) == 0.75
    assert ctrf_pass_fraction({"tests": 4, "passed": 4, "failed": []}) == 1.0
    assert ctrf_pass_fraction({"tests": 4, "passed": 0, "failed": []}) == 0.0
    # No usable report -> the caller keeps the sparse reward.
    assert ctrf_pass_fraction(None) is None
    assert ctrf_pass_fraction({"tests": 0, "passed": 0, "failed": []}) is None


def test_dense_mode_replaces_the_binary_reward(monkeypatch) -> None:
    """3-of-4 tests is reward 0 under sparse and 0.75 under dense."""
    report = {"tests": 4, "passed": 3, "failed": ["t::test_check_04_no_shortcut"]}
    rollouter = _stub_rollouter(
        monkeypatch, ctrf_result=report, reward_mode="dense", sparse_reward=0.0
    )
    rollout, _, _, _, _ = _run_rollout(rollouter)

    assert rollout.turns[-1].env_rewards == {"tmax_reward": 0.75}
    # The binary value stays recorded so both curves are comparable on one run.
    assert rollout.diagnostics["sparse_reward"] == 0.0
    assert rollout.diagnostics["dense_fallback"] is False


def test_dense_mode_falls_back_to_sparse_without_a_report(monkeypatch) -> None:
    """The ~6% of tasks with no CTRF report must keep the verifier's binary reward."""
    rollouter = _stub_rollouter(
        monkeypatch, ctrf_result=None, reward_mode="dense", sparse_reward=1.0
    )
    rollout, _, _, _, _ = _run_rollout(rollouter)

    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}
    assert rollout.diagnostics["dense_fallback"] is True


def test_dense_mode_falls_back_when_the_read_raises(monkeypatch) -> None:
    """A failed CTRF read degrades to the sparse reward, never to a lost rollout."""
    rollouter = _stub_rollouter(
        monkeypatch,
        ctrf_result=RuntimeError("sandbox lost"),
        reward_mode="dense",
        sparse_reward=1.0,
    )
    rollout, _, _, _, diagnostics = _run_rollout(rollouter)

    assert rollout.turns[-1].env_rewards == {"tmax_reward": 1.0}
    assert rollout.status == RolloutStatus.COMPLETED
    assert diagnostics.infra_failed is False
    assert rollout.diagnostics["dense_fallback"] is True


def test_sparse_mode_ignores_the_report(monkeypatch) -> None:
    """Reading the report for metrics must not perturb the sparse reward."""
    report = {"tests": 4, "passed": 3, "failed": ["t::test_check_04_no_shortcut"]}
    rollouter = _stub_rollouter(
        monkeypatch, ctrf_result=report, reward_mode="sparse", sparse_reward=0.0
    )
    rollout, _, _, _, _ = _run_rollout(rollouter)

    assert rollout.turns[-1].env_rewards == {"tmax_reward": 0.0}
    assert rollout.diagnostics["dense_fallback"] is False


def test_unknown_reward_mode_is_rejected() -> None:
    config = TMaxRollouter.Config(reward_mode="partial")
    with pytest.raises(ValueError, match="reward_mode must be one of"):
        TMaxRollouter(config)


def test_dense_mode_leaves_validation_on_the_binary_verdict(monkeypatch) -> None:
    """Validation groups carry negative ids and must keep the sparse reward.

    TB-2.0 avg@k / pass@k are defined on the all-tests-pass verdict, so a dense
    validation reward would stop being a solve rate and stop being comparable to
    the published numbers and to earlier runs.
    """
    report = {"tests": 4, "passed": 3, "failed": ["t::test_check_04_no_shortcut"]}
    rollouter = _stub_rollouter(
        monkeypatch, ctrf_result=report, reward_mode="dense", sparse_reward=0.0
    )
    rollout, _, _, _, _ = _run_rollout(rollouter, group_id=-7)

    assert rollout.turns[-1].env_rewards == {"tmax_reward": 0.0}
    # Still recorded for metrics -- just not used as the reward.
    assert rollout.diagnostics["ctrf"] == report
