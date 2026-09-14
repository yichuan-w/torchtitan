# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""``_guard_eval_generators``: an eval-generator failure must not disable
validation for the rest of the run -- and must not hang it either.

An eval generator idle between passes answers its next call with a gloo
"connection closed by peer" and is fine on the retry. The guard used to drop the
router on the first exception, which cost a whole TB-2.0 eval curve: a single
blip at step 20 left only the step-0 point for the rest of a 100-step run.

The retry that fixed it then caused the opposite failure. A dropped gloo pair
killed an eval generator's engine loop AND failed its weight pull in 6 ms; the
retry 5 s later addressed a dead actor, which does not raise -- it stops
answering. With no deadline the controller sat in that await for 8.7 hours
holding 15 hosts, while MAST still reported RUNNING because the process was alive.
So the retry is only safe with a timeout, and a hang has to count as a failure.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from torchtitan.experiments.rl import controller as controller_mod
from torchtitan.experiments.rl.controller import (
    _EVAL_GUARD_ATTEMPTS,
    _EVAL_GUARD_MAX_FAILURES,
    Controller,
)
from torchtitan.experiments.rl.eval_trace_recorder import ValidationTraceRecorder
from torchtitan.experiments.rl.rollout import Rollout, RolloutGroup, RolloutStatus


@pytest.mark.parametrize(
    "failure", [None, "infra", "group", "short", "none", "nan", "inf"]
)
def test_incomplete_validation_logs_scores_with_requested_denominator(
    failure, tmp_path, monkeypatch
):
    reward = {"none": None, "nan": float("nan"), "inf": float("inf")}.get(failure, 1.0)
    group = RolloutGroup(
        group_id=-1,
        rollouts=[
            Rollout(
                group_id=-1,
                rollout_id=0,
                status=RolloutStatus.COMPLETED,
                reward=reward,
                diagnostics={"infra_failed": failure == "infra"},
            )
        ],
    )

    async def run_group(**kwargs):
        if kwargs["group_id"] == -2:
            return RolloutGroup(
                group_id=-2,
                rollouts=[
                    Rollout(
                        group_id=-2,
                        rollout_id=i,
                        status=RolloutStatus.COMPLETED,
                        reward=1.0,
                    )
                    for i in range(5)
                ],
            )
        if failure == "group":
            raise RuntimeError("sandbox unavailable")
        if failure != "short":
            group.rollouts.extend(
                Rollout(
                    group_id=-1,
                    rollout_id=i,
                    status=RolloutStatus.COMPLETED,
                    reward=0.0,
                )
                for i in range(1, 5)
            )
        return group

    collector = SimpleNamespace(
        _rollouter=SimpleNamespace(
            get_validation_sample=iter(["task-a", "task-b"]).__next__
        ),
        _allocate_validation_group_ids=lambda count: [-1, -2],
        _eval_rollout_workers=[],
        _run_validation_group=run_group,
    )
    samples, groups, metrics = asyncio.run(
        Controller._collect_validation_rollouts(
            collector,
            num_groups=2,
            group_size=5,
            sampling=None,
            step=40,
        )
    )
    aggregated = controller_mod.m.MetricsProcessor._aggregate_metrics(metrics)
    first_passes = failure in (None, "short")
    assert aggregated["validation/valid"] == float(failure is None)
    assert aggregated["validation_reward/_mean"] == (0.6 if first_passes else 0.5)
    assert aggregated["validation/pass_at_k/mean"] == (1.0 if first_passes else 0.5)
    assert len(groups) == (1 if failure == "group" else 2)
    if failure == "infra":
        assert groups[0].rollouts[0].reward == 1.0
    recorder = SimpleNamespace(
        validation_trace_recorder=ValidationTraceRecorder.Config(enable=True).build(
            dump_dir=str(tmp_path)
        ),
        config=SimpleNamespace(
            async_loop=SimpleNamespace(
                validation=SimpleNamespace(num_samples=2, group_size=5)
            )
        ),
        renderer=SimpleNamespace(_tokenizer=SimpleNamespace(decode=lambda tokens: "")),
    )
    summary = Controller._record_validation_traces(
        recorder,
        step=40,
        samples=samples,
        rollout_groups=groups,
    )
    assert summary.avg_at_k == aggregated["validation_reward/_mean"]
    assert summary.pass_at_k == aggregated["validation/pass_at_k/mean"]
    assert summary.valid == (failure is None)
    fake_wandb = MagicMock()
    fake_wandb.run = object()
    monkeypatch.setitem(sys.modules, "wandb", fake_wandb)
    Controller._mirror_validation_to_wandb(
        SimpleNamespace(), aggregated, policy_version=40
    )
    payload = fake_wandb.log.call_args.args[0]
    assert payload["validation_reward/_mean"] == summary.avg_at_k
    assert payload["validation/pass_at_k/mean"] == summary.pass_at_k
    assert payload["validation/valid"] == float(summary.valid)


class _Guard:
    """The guard bound to a stand-in with just the state it touches."""

    def __init__(self) -> None:
        self.eval_generator_router = object()
        self._eval_rollout_workers = [object()]
        self._eval_guard_failures = 0

    guard = Controller._guard_eval_generators

    @property
    def disabled(self) -> bool:
        return self.eval_generator_router is None


def _flaky(num_failures: int):
    """A call factory that raises ``num_failures`` times, then succeeds."""
    state = {"calls": 0}

    async def make():
        state["calls"] += 1
        if state["calls"] <= num_failures:
            raise RuntimeError("gloo: Connection closed by peer")

    return make, state


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    """Skip the retry backoff so the tests do not wait on wall-clock."""
    real_sleep = asyncio.sleep

    async def instant(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", instant)


def test_retry_recovers_without_disabling():
    g = _Guard()
    make, state = _flaky(_EVAL_GUARD_ATTEMPTS - 1)

    assert asyncio.run(g.guard(make, what="pull")) is True
    assert state["calls"] == _EVAL_GUARD_ATTEMPTS, "each retry must re-issue the RPC"
    assert not g.disabled
    assert g._eval_guard_failures == 0


def test_one_exhausted_window_keeps_the_evaluator():
    """A whole failed window costs one validation point, not the curve."""
    g = _Guard()
    make, state = _flaky(999)

    assert asyncio.run(g.guard(make, what="pull")) is False
    assert state["calls"] == _EVAL_GUARD_ATTEMPTS
    assert not g.disabled, "the next step must get another chance"
    assert g._eval_guard_failures == 1


def test_disabled_only_after_consecutive_exhausted_windows():
    g = _Guard()
    make, _ = _flaky(999)

    for i in range(1, _EVAL_GUARD_MAX_FAILURES):
        assert asyncio.run(g.guard(make, what="pull")) is False
        assert not g.disabled, f"still up after {i} failed window(s)"

    assert asyncio.run(g.guard(make, what="pull")) is False
    assert g.disabled
    assert g._eval_rollout_workers == []


def test_success_resets_the_failure_streak():
    g = _Guard()
    failing, _ = _flaky(999)
    ok, _ = _flaky(0)

    # One short of the limit, then a success, then failures again: the streak must
    # restart, so the evaluator survives.
    for _ in range(_EVAL_GUARD_MAX_FAILURES - 1):
        asyncio.run(g.guard(failing, what="pull"))
    assert asyncio.run(g.guard(ok, what="pull")) is True
    assert g._eval_guard_failures == 0
    for _ in range(_EVAL_GUARD_MAX_FAILURES - 1):
        asyncio.run(g.guard(failing, what="pull"))
    assert not g.disabled


@pytest.fixture
def _short_timeout(monkeypatch):
    """Make the weight-pull deadline fire fast enough to test."""
    monkeypatch.setattr(controller_mod, "_WEIGHT_PULL_TIMEOUT_SEC", 0.05)


def _hanging():
    """A call factory whose RPC never answers -- a dead actor, not a raising one."""
    state = {"calls": 0}

    async def make():
        state["calls"] += 1
        await asyncio.Event().wait()

    return make, state


def test_a_hanging_call_is_a_failed_attempt_not_a_hang(_short_timeout):
    """The regression: this used to block the trainer loop forever."""
    g = _Guard()
    make, state = _hanging()

    async def run():
        return await asyncio.wait_for(g.guard(make, what="pull"), timeout=10)

    assert asyncio.run(run()) is False
    assert state["calls"] == _EVAL_GUARD_ATTEMPTS, "a hang must still be retried"
    assert not g.disabled, "one bad window costs a validation point, not the curve"
    assert g._eval_guard_failures == 1


def test_a_hang_then_a_recovery_still_counts_as_success(_short_timeout):
    """The real sequence: the pull raises, and the retry finds a dead actor."""
    g = _Guard()
    state = {"calls": 0}

    async def make():
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("gloo: Connection closed by peer")
        if state["calls"] == 2:
            await asyncio.Event().wait()  # the actor is gone

    async def run():
        return await asyncio.wait_for(g.guard(make, what="pull"), timeout=10)

    assert asyncio.run(run()) is True, "the third attempt succeeds"
    assert state["calls"] == 3
    assert g._eval_guard_failures == 0


def test_cancellation_propagates_and_never_disables():
    """A shutdown must not be recorded as an evaluator failure."""
    g = _Guard()

    async def make():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(g.guard(make, what="pull"))
    assert not g.disabled
    assert g._eval_guard_failures == 0
