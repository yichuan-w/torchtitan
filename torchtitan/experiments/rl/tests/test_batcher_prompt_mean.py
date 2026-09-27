# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""``loss_aggregation="prompt_mean"`` weighs every rollout group equally.

It is applied by rescaling advantages inside the batcher, so the claim to defend
is numerical: the trainer's unchanged ``sum(-A * ratio) / D`` must equal
``mean over groups of (mean over the group's tokens of -A * ratio)``.
"""

from __future__ import annotations

import pytest
import torch

from torchtitan.experiments.rl.components.batcher import BatchConfig, Batcher
from torchtitan.experiments.rl.losses.dppo import DPPOLoss
from torchtitan.experiments.rl.types import (
    RolloutTurnID,
    TrainingSample,
    TrainingSampleGroup,
)

_SEQ_LEN = 128
_VOCAB = 64


def _sample(*, group: int, idx: int, advantage: float, num_completion: int, token: int):
    num_prompt = 2
    n = num_prompt + num_completion
    return TrainingSample(
        min_policy_version=0,
        max_policy_version=0,
        rollout_id=RolloutTurnID(group_id=group, rollout_id=idx, turn_id=0),
        token_ids=[token] * n,
        loss_mask=[False] * num_prompt + [True] * num_completion,
        logprobs=[0.0] * num_prompt + [-0.5] * num_completion,
        advantage=[0.0] * num_prompt + [advantage] * num_completion,
    )


def _groups():
    # Group 0: short trajectories (4 tokens). Group 1: long ones (16 tokens).
    # Group 2: zero-variance, no signal.
    return [
        TrainingSampleGroup(
            group_id=0,
            training_samples=[
                _sample(group=0, idx=0, advantage=0.5, num_completion=4, token=3),
                _sample(group=0, idx=1, advantage=-0.5, num_completion=4, token=4),
            ],
            metrics=[],
        ),
        TrainingSampleGroup(
            group_id=1,
            training_samples=[
                _sample(group=1, idx=0, advantage=0.5, num_completion=16, token=5),
                _sample(group=1, idx=1, advantage=-0.5, num_completion=16, token=6),
            ],
            metrics=[],
        ),
        TrainingSampleGroup(
            group_id=2,
            training_samples=[
                _sample(group=2, idx=0, advantage=0.0, num_completion=8, token=7),
                _sample(group=2, idx=1, advantage=0.0, num_completion=8, token=8),
            ],
            metrics=[],
        ),
    ]


def _batch(*, aggregation: str, skip: bool = True, exclude: bool = False):
    batcher = Batcher.Config(
        batch=BatchConfig(local_batch_size=2, seq_len=_SEQ_LEN),
        skip_zero_advantage_samples=skip,
        zero_advantage_tokens_in_loss_denominator=not exclude,
        loss_aggregation=aggregation,
    ).build(num_groups_per_train_step=3, dp_degree=1, pad_id=0, initial_policy_version=0)
    batch = None
    for group in _groups():
        batch = batcher.add_training_samples(training_sample_group=group)
    assert batch is not None
    return batch


def _loss_and_grad(batch) -> tuple[float, torch.Tensor]:
    loss_fn = DPPOLoss.Config().build()
    weight = torch.full((_VOCAB, _VOCAB), 0.1, requires_grad=True)
    total = torch.zeros(())
    for row in batch.microbatches:
        for microbatch in row:
            loss, _ = loss_fn(
                weight[microbatch.token_ids],
                microbatch.labels,
                batch.num_loss_denominator_tokens,
                generator_logprobs=microbatch.generator_logprobs,
                advantages=microbatch.advantages,
                loss_mask=microbatch.loss_mask,
            )
            loss.backward()
            total += loss.detach()
    return float(total), weight.grad


def _reference_prompt_mean() -> tuple[float, torch.Tensor]:
    """Each signal group alone, token-mean within it, then averaged over groups."""
    losses, grads = [], []
    for group in _groups()[:2]:
        batcher = Batcher.Config(
            batch=BatchConfig(local_batch_size=2, seq_len=_SEQ_LEN)
        ).build(num_groups_per_train_step=1, dp_degree=1, pad_id=0, initial_policy_version=0)
        loss, grad = _loss_and_grad(batcher.add_training_samples(training_sample_group=group))
        losses.append(loss)
        grads.append(grad)
    return sum(losses) / 2, (grads[0] + grads[1]) / 2


@pytest.mark.parametrize("skip,exclude", [(True, False), (False, False), (True, True)])
def test_prompt_mean_equals_the_mean_of_per_group_token_means(skip, exclude):
    loss, grad = _loss_and_grad(_batch(aggregation="prompt_mean", skip=skip, exclude=exclude))
    ref_loss, ref_grad = _reference_prompt_mean()
    torch.testing.assert_close(loss, ref_loss, rtol=1e-5, atol=1e-7)
    torch.testing.assert_close(grad, ref_grad, rtol=1e-5, atol=1e-7)
    assert ref_grad.abs().sum() > 0.0


def test_token_mean_is_the_default_and_differs_when_lengths_differ():
    default = Batcher.Config()
    assert default.loss_aggregation == "token_mean"
    _, token_grad = _loss_and_grad(_batch(aggregation="token_mean"))
    _, prompt_grad = _loss_and_grad(_batch(aggregation="prompt_mean"))
    assert not torch.allclose(token_grad, prompt_grad)


def test_token_mean_path_leaves_advantages_untouched():
    batch = _batch(aggregation="token_mean")
    values = {
        round(float(v), 6)
        for row in batch.microbatches
        for mb in row
        for v in mb.advantages[mb.loss_mask]
    }
    assert values == {0.5, -0.5}


def test_prompt_mean_metrics():
    batch = _batch(aggregation="prompt_mean")
    got = {metric.key: metric.value.value for metric in batch.metrics if "prompt_mean" in metric.key}
    assert got["train_batch/prompt_mean_num_groups"] == 2.0
    # D counts every valid token (default denominator): 2*4 + 2*16 + 2*8 = 56.
    assert got["train_batch/prompt_mean_scale_max"] == pytest.approx(56 / (2 * 8))
    assert got["train_batch/prompt_mean_scale_min"] == pytest.approx(56 / (2 * 32))


def test_unknown_aggregation_is_rejected():
    with pytest.raises(ValueError, match="loss_aggregation"):
        Batcher.Config(loss_aggregation="seq_mean").build(
            num_groups_per_train_step=1, dp_degree=1, pad_id=0, initial_policy_version=0
        )
