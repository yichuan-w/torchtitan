# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""An infrastructure failure is not a verdict, so it must not become one.

A reward of NaN means nothing scored this rollout -- the sandbox died, the wall
clock ran out -- as opposed to 0.0, which is a verdict of failure. The two used to
be the same value, on the reasoning that a failed rollout has no completion and so
contributes no training tokens. That holds for a sandbox that never booted and not
for the timeouts that dominate: across a 445-trial Terminal-Bench 2.0 pass, all 30
infrastructure failures carried tokens, a median of 62 turns and 45k completion
tokens each.

Scored as a 0, such a rollout does two kinds of damage under centered advantage:
its own turns get ``0 - group_mean``, a negative advantage training the policy away
from behavior no verdict established was wrong, and its 0 drags the baseline down
for every sibling. So the unscored rollout leaves before any group statistic is
taken -- a group of 8 holding one of them baselines and packs as 7.
"""

from __future__ import annotations

import math

import pytest

from torchtitan.experiments.rl.components.training_sample_builder import (
    TrainingSampleBuilder,
)
from torchtitan.experiments.rl.controller_metrics import compute_rollout_metrics
from torchtitan.experiments.rl.observability import metrics as m
from torchtitan.experiments.rl.rollout.advantage import AdvantageEstimator
from torchtitan.experiments.rl.rollout.types import (
    Rollout,
    RolloutGroup,
    RolloutStatus,
    RolloutTurn,
)
from torchtitan.experiments.rl.types import RolloutTurnID


def _rollout(*, group_id: int, rollout_id: int, reward: float) -> Rollout:
    """A rollout with one trainable turn, so it is not dropped as untrainable."""
    rollout = Rollout(
        group_id=group_id, rollout_id=rollout_id, status=RolloutStatus.COMPLETED
    )
    rollout.reward = reward
    rollout.turns = [
        RolloutTurn(
            rollout_id=RolloutTurnID(
                group_id=group_id, rollout_id=rollout_id, turn_id=0
            ),
            prompt_token_ids=[1, 2, 3],
            completion_token_ids=[4, 5],
            completion_logprobs=[-0.1, -0.2],
            min_policy_version=0,
            max_policy_version=0,
        )
    ]
    return rollout


def _group(rewards: list[float], *, group_id: int = 0) -> RolloutGroup:
    return RolloutGroup(
        group_id=group_id,
        rollouts=[
            _rollout(group_id=group_id, rollout_id=i, reward=reward)
            for i, reward in enumerate(rewards)
        ],
    )


def _estimator(*, std_normalize: bool = False) -> AdvantageEstimator:
    return AdvantageEstimator.Config(should_std_normalize=std_normalize).build()


# --------------------------------------------------------------------------
# The baseline
# --------------------------------------------------------------------------


def test_the_baseline_skips_an_unscored_rollout():
    """8 siblings, 2 solved, 1 unscored -> the baseline is over the surviving 7."""
    rewards = [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, math.nan]
    advantages = _estimator()(_group(rewards))

    # mean over the 7 scored = 2/7, NOT 2/8.
    assert advantages[0] == 1.0 - 2.0 / 7.0
    assert advantages[2] == 0.0 - 2.0 / 7.0
    assert math.isnan(advantages[7])


def test_scoring_it_as_zero_would_have_shifted_every_sibling():
    """The regression, stated as a difference.

    A spurious 0 in the group pulls the baseline down (2/8 < 2/7), which inflates
    every solver's advantage -- credit for clearing a bar that was set too low -- and
    hands the failure itself a negative advantage it did nothing to earn.
    """
    unscored = _estimator()(_group([1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, math.nan]))
    as_zero = _estimator()(_group([1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))

    assert as_zero[0] == 1.0 - 2.0 / 8.0  # 0.750
    assert unscored[0] == 1.0 - 2.0 / 7.0  # 0.714
    assert as_zero[0] > unscored[0]  # solvers were over-credited
    assert as_zero[7] == -2.0 / 8.0  # the failure was trained away from
    assert math.isnan(unscored[7])  # now it is simply not trained


def test_the_std_denominator_also_skips_it():
    """Standard GRPO divides by the group std, which the unscored 0 would deflate."""
    rewards = [1.0, 0.0, math.nan]
    advantages = _estimator(std_normalize=True)(_group(rewards))

    # pstdev over [1.0, 0.0] = 0.5, not pstdev over [1.0, 0.0, 0.0].
    assert advantages[0] == (1.0 - 0.5) / (0.5 + 1e-6)
    assert math.isnan(advantages[2])


def test_an_entirely_unscored_group_has_no_baseline():
    """Every sibling died on infrastructure: there is nothing to center against, and
    the mean must not be taken over an empty sequence."""
    advantages = _estimator()(_group([math.nan, math.nan]))

    assert all(math.isnan(advantage) for advantage in advantages)


def test_a_group_with_no_failures_is_unchanged():
    """The common path has to be bit-for-bit what it was."""
    assert _estimator()(_group([1.0, 0.0, 0.0, 1.0])) == [0.5, -0.5, -0.5, 0.5]


# --------------------------------------------------------------------------
# The sample builder
# --------------------------------------------------------------------------


def _build(rewards: list[float], **config_kwargs):
    builder = TrainingSampleBuilder.Config(
        drop_zero_std_reward_groups=False,
        drop_groups_with_untrainable_rollouts=False,
        **config_kwargs,
    ).build()
    group = _group(rewards)
    for rollout, advantage in zip(group.rollouts, _estimator()(group), strict=True):
        rollout.advantage = advantage
    return builder.build_from_group(rollout_group=group)


def _metric(result, key: str):
    for metric in result.metrics:
        if metric.key == key:
            return metric.value.value
    return None


def test_an_unscored_rollout_is_not_packed():
    """It has completion tokens, so nothing else would have excluded it."""
    result = _build([1.0, 0.0, math.nan])

    assert len(result.training_samples) == 2
    assert _metric(result, "training_sample_builder/num_unscored_rollouts_dropped") == 1


def test_group_statistics_are_taken_over_survivors():
    """avg_train_reward is the solve fraction of the group; an unscored sibling must
    not count in its denominator."""
    result = _build([1.0, 0.0, math.nan])

    assert _metric(result, "rollout_reward/avg_train_reward") == 0.5


def test_an_unscored_sibling_does_not_fake_reward_variance():
    """All survivors solved, so the group is zero-std and carries no signal. Left in,
    the NaN would make pstdev NaN and the group would look trainable."""
    result = _build([1.0, 1.0, math.nan])

    assert _metric(result, "rollout_reward/group_zero_std_frac") == 1.0
    assert _metric(result, "rollout_reward/group_all_solved_frac") == 1.0


def test_an_entirely_unscored_group_is_dropped_without_raising():
    """statistics.mean would raise on the empty survivor list."""
    result = _build([math.nan, math.nan])

    assert result.training_samples == []
    assert _metric(result, "training_sample_builder/num_groups_dropped_unscored") == 1.0


def test_a_group_with_no_failures_packs_everything():
    result = _build([1.0, 0.0])

    assert len(result.training_samples) == 2
    assert (
        _metric(result, "training_sample_builder/num_unscored_rollouts_dropped") is None
    )


@pytest.mark.parametrize("drop_empty", [False, True])
@pytest.mark.parametrize("failed_reward", [0.0, math.nan])
def test_empty_sibling_group_drop_depends_on_scoring_and_config(
    drop_empty, failed_reward
):
    group = _group([1.0, 0.0, failed_reward])
    group.rollouts[-1].turns = []
    for rollout, advantage in zip(group.rollouts, _estimator()(group), strict=True):
        rollout.advantage = advantage
    builder = TrainingSampleBuilder.Config(
        drop_zero_std_reward_groups=True,
        drop_groups_with_untrainable_rollouts=drop_empty,
    ).build()
    result = builder.build_from_group(rollout_group=group)
    expected = 0 if drop_empty and not math.isnan(failed_reward) else 2
    assert len(result.training_samples) == expected


def test_rollout_reward_metrics_skip_unscored_rollouts():
    metrics = compute_rollout_metrics(
        prefix="rollout", rollouts=_group([1.0, 0.0, math.nan]).rollouts
    )

    reward_metric = next(metric for metric in metrics if metric.key == "rollout_reward")
    assert isinstance(reward_metric.value, m.SummaryStats)
    assert reward_metric.value.total == 1.0
    assert reward_metric.value.count == 2.0
