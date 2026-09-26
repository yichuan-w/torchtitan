# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""MiMo-style in-group length penalty on a training group's solves."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl.examples.tmax.rollouter import TMaxRollouter
from torchtitan.experiments.rl.rollout.types import Rollout, RolloutStatus, RolloutTurn
from torchtitan.experiments.rl.types import RolloutTurnID


def _rollout(reward: float, turns: int, tokens_per_turn: int, idx: int) -> Rollout:
    return Rollout(
        group_id=0,
        rollout_id=idx,
        turns=[
            RolloutTurn(
                rollout_id=RolloutTurnID(group_id=0, rollout_id=idx, turn_id=t),
                prompt_token_ids=[1],
                completion_token_ids=[2] * tokens_per_turn,
                completion_logprobs=[0.0] * tokens_per_turn,
                min_policy_version=0,
                max_policy_version=0,
            )
            for t in range(turns)
        ],
        status=RolloutStatus.COMPLETED,
        reward=reward,
    )


def _rollouter(**overrides):
    cfg = dict(
        _length_penalty_max=0.1,
        _length_penalty_deadzone=0.3,
        _length_penalty_saturate=1.0,
        _length_penalty_exponent=1.5,
        _length_penalty_min_pass_rate=0.5,
    )
    cfg.update(overrides)
    return SimpleNamespace(**cfg)


def _apply(rollouter, rollouts):
    return TMaxRollouter._apply_length_penalty(rollouter, rollouts)


def _mimo_penalty(excess: float) -> float:
    if excess <= 0.3:
        return 0.0
    return 0.1 * min((excess - 0.3) / 0.7, 1.0) ** 1.5


def test_solves_are_ranked_against_the_group_median():
    # Three solves at 20, 20, 40 turns (median 20) and one failure: pass rate 0.75.
    a = _rollout(1.0, 20, 100, 0)
    b = _rollout(1.0, 20, 100, 1)
    long = _rollout(1.0, 40, 100, 2)  # excess 1.0 on both signals -> full penalty
    failed = _rollout(0.0, 90, 100, 3)
    _apply(_rollouter(), [a, b, long, failed])
    assert a.reward == b.reward == 1.0
    assert long.reward == pytest.approx(1.0 - 0.1)
    assert failed.reward == 0.0


def test_ramp_matches_mimo_between_deadzone_and_saturate():
    base = [_rollout(1.0, 10, 100, i) for i in range(3)]
    mid = _rollout(1.0, 16, 100, 3)  # median of [10,10,10,16] = 10 -> excess 0.6
    _apply(_rollouter(), [*base, mid])
    assert mid.reward == pytest.approx(1.0 - _mimo_penalty(0.6))


def test_generated_tokens_count_even_at_equal_turns():
    base = [_rollout(1.0, 10, 100, i) for i in range(3)]
    verbose = _rollout(1.0, 10, 300, 3)  # same turns, 3x the tokens -> excess 2.0
    _apply(_rollouter(), [*base, verbose])
    assert verbose.reward == pytest.approx(0.9)


def test_all_solved_group_is_shaped():
    rollouts = [_rollout(1.0, 20, 100, i) for i in range(3)] + [_rollout(1.0, 80, 100, 3)]
    _apply(_rollouter(), rollouts)
    assert [r.reward for r in rollouts[:3]] == [1.0, 1.0, 1.0]
    assert rollouts[3].reward < 1.0


def test_low_pass_rate_group_is_skipped():
    # 2 of 4 solved: 0.5 is not strictly greater than min_pass_rate.
    rollouts = [
        _rollout(1.0, 10, 100, 0),
        _rollout(1.0, 80, 100, 1),
        _rollout(0.0, 10, 100, 2),
        _rollout(0.0, 10, 100, 3),
    ]
    assert _apply(_rollouter(), rollouts) == []
    assert rollouts[1].reward == 1.0


def test_unscored_siblings_do_not_count():
    solves = [_rollout(1.0, 10, 100, 0), _rollout(1.0, 30, 100, 1)]
    infra = _rollout(math.nan, 10, 100, 2)
    _apply(_rollouter(), [*solves, infra])
    assert math.isnan(infra.reward)
    assert solves[1].reward < 1.0  # pass rate over scored siblings is 1.0


def test_a_penalized_solve_still_counts_as_solved():
    rollouts = [_rollout(1.0, 1, 1, 0), _rollout(1.0, 1000, 1000, 1)]
    _apply(_rollouter(), rollouts)
    assert rollouts[1].reward > 0.0


def test_config_rejects_a_penalty_that_could_zero_a_solve():
    with pytest.raises(ValueError, match="length_penalty_max"):
        TMaxRollouter(TMaxRollouter.Config(length_penalty_max=1.0))
