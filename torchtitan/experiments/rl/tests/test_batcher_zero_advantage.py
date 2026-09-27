# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Skipping zero-advantage samples buys compute, not a different update.

``skip_zero_advantage_samples`` exists to make ``drop_zero_std_reward_groups=False``
affordable: with the drop off, most groups are all-solved or all-failed, their
centered advantage is 0, and ``-advantage * ratio`` is identically 0 for every one
of their tokens. The claim these tests defend is that removing them from the
forward pass leaves the optimizer's view of the batch alone -- same loss, same
gradient, same denominator -- while shrinking what the trainer computes.
"""

from __future__ import annotations

import torch

from torchtitan.experiments.rl.components.batcher import BatchConfig, Batcher
from torchtitan.experiments.rl.losses.dppo import DPPOLoss
from torchtitan.experiments.rl.types import TrainingSample, TrainingSampleGroup

_SEQ_LEN = 64
_PAD_ID = 0


def _sample(*, advantage: float, token: int, num_tokens: int = 8) -> TrainingSample:
    """One sample whose completion half carries ``advantage``."""
    num_prompt = num_tokens // 2
    num_completion = num_tokens - num_prompt
    return TrainingSample(
        min_policy_version=0,
        max_policy_version=0,
        rollout_id=None,
        token_ids=[token] * num_tokens,
        loss_mask=[False] * num_prompt + [True] * num_completion,
        logprobs=[0.0] * num_prompt + [-0.5] * num_completion,
        advantage=[0.0] * num_prompt + [advantage] * num_completion,
    )


def _batch(*, skip: bool, samples: list[TrainingSample]):
    batcher = Batcher.Config(
        batch=BatchConfig(local_batch_size=2, seq_len=_SEQ_LEN),
        skip_zero_advantage_samples=skip,
    ).build(
        num_groups_per_train_step=1,
        dp_degree=1,
        pad_id=_PAD_ID,
        initial_policy_version=0,
    )
    return batcher.add_training_samples(
        training_sample_group=TrainingSampleGroup(
            group_id=0, training_samples=samples, metrics=[]
        )
    )


# A realistic zero-std batch: two samples carry signal, six do not.
def _mixed_samples() -> list[TrainingSample]:
    return [
        _sample(advantage=0.875, token=11),
        _sample(advantage=-0.125, token=12),
        *[_sample(advantage=0.0, token=20 + i) for i in range(6)],
    ]


def test_denominator_is_unchanged_by_the_skip():
    """The loss denominator counts consumed tokens, not surviving ones."""
    samples = _mixed_samples()
    assert (
        _batch(skip=True, samples=samples).num_global_valid_tokens
        == _batch(skip=False, samples=samples).num_global_valid_tokens
    )


def test_denominator_still_equals_the_packed_loss_mask_sum():
    """Computing the denominator from samples must match summing the packed rows.

    The pre-filter denominator is derived from ``loss_mask[1:]`` rather than from the
    packed rows it used to be summed over. Padding masks to False, so the two agree --
    but only as long as that stays true of both per-sample and row padding.
    """
    batch = _batch(skip=False, samples=_mixed_samples())
    packed_true = sum(
        int(microbatch.loss_mask.sum())
        for row in batch.microbatches
        for microbatch in row
    )
    assert batch.num_global_valid_tokens == packed_true


def test_skip_removes_the_zero_advantage_tokens_from_the_forward_pass():
    """The point of the flag: fewer tokens actually reach the model."""
    samples = _mixed_samples()
    kept, dropped = (
        sum(
            int(microbatch.loss_mask.sum())
            for row in _batch(skip=skip, samples=samples).microbatches
            for microbatch in row
        )
        for skip in (True, False)
    )
    # Two of eight samples carry advantage, each with 4 completion tokens. The label
    # shift drops token 0, which is a prompt token, so trained counts are unaffected.
    assert (kept, dropped) == (2 * 4, 8 * 4)


def test_loss_and_gradient_are_unchanged_by_the_skip():
    """The real claim: same loss, and the same gradient into the model's parameters.

    The two packings put the same tokens at different (row, position) offsets, so the
    gradient is only comparable through something indexed by token id rather than by
    position. Hence the stand-in model: logits are a lookup of a fixed ``[V, V]``
    weight, and the assertion is on the accumulated ``dL/dW``.

    Mathematically the two are identical, so ``==`` would be the honest assert -- but
    repacking reorders the float additions, so this uses a tolerance instead.
    """
    vocab_size = 32
    samples = _mixed_samples()
    loss_fn = DPPOLoss.Config().build()

    def loss_and_grad(*, skip: bool) -> tuple[float, torch.Tensor]:
        batch = _batch(skip=skip, samples=samples)
        weight = torch.full((vocab_size, vocab_size), 0.1)
        weight.requires_grad_(True)
        total_loss = torch.zeros(())
        for row in batch.microbatches:
            for microbatch in row:
                loss, _ = loss_fn(
                    weight[microbatch.token_ids],
                    microbatch.labels,
                    batch.num_global_valid_tokens,
                    generator_logprobs=microbatch.generator_logprobs,
                    advantages=microbatch.advantages,
                    loss_mask=microbatch.loss_mask,
                )
                loss.backward()
                total_loss += loss.detach()
        return float(total_loss), weight.grad

    skipped_loss, skipped_grad = loss_and_grad(skip=True)
    full_loss, full_grad = loss_and_grad(skip=False)

    torch.testing.assert_close(skipped_loss, full_loss, rtol=1e-6, atol=1e-8)
    torch.testing.assert_close(skipped_grad, full_grad, rtol=1e-6, atol=1e-8)
    # Guard against a vacuous pass: a batch with no gradient anywhere would satisfy
    # the equalities above.
    assert full_grad.abs().sum() > 0.0


def test_skip_is_a_no_op_when_every_sample_has_advantage():
    """With drop_zero_std on, the flag must change nothing at all."""
    samples = [_sample(advantage=0.5, token=11), _sample(advantage=-0.5, token=12)]
    skipped = _batch(skip=True, samples=samples)
    full = _batch(skip=False, samples=samples)
    assert len(skipped.microbatches) == len(full.microbatches)
    for skipped_row, full_row in zip(skipped.microbatches, full.microbatches):
        for skipped_mb, full_mb in zip(skipped_row, full_row):
            torch.testing.assert_close(skipped_mb.advantages, full_mb.advantages)
            torch.testing.assert_close(skipped_mb.token_ids, full_mb.token_ids)


def test_an_all_zero_advantage_batch_degrades_to_a_zero_gradient_step():
    """Every group zero-std: the true gradient is zero, so an empty forward is correct."""
    batch = _batch(
        skip=True, samples=[_sample(advantage=0.0, token=20 + i) for i in range(4)]
    )
    assert batch.num_global_valid_tokens > 0  # the tokens were still consumed
    assert all(
        not bool(microbatch.loss_mask.any())
        for row in batch.microbatches
        for microbatch in row
    )


def _batch_with_denominator(
    *, skip: bool, exclude: bool, samples: list[TrainingSample]
):
    batcher = Batcher.Config(
        batch=BatchConfig(local_batch_size=2, seq_len=_SEQ_LEN),
        skip_zero_advantage_samples=skip,
        zero_advantage_tokens_in_loss_denominator=not exclude,
    ).build(
        num_groups_per_train_step=1,
        dp_degree=1,
        pad_id=_PAD_ID,
        initial_policy_version=0,
    )
    return batcher.add_training_samples(
        training_sample_group=TrainingSampleGroup(
            group_id=0, training_samples=samples, metrics=[]
        )
    )


def test_default_loss_denominator_counts_every_valid_token():
    """The default keeps the loss scale this file defends above."""
    batch = _batch(skip=True, samples=_mixed_samples())
    assert batch.num_loss_denominator_tokens == batch.num_global_valid_tokens


def test_excluding_zero_advantage_tokens_counts_only_signal_tokens():
    """Two of eight samples carry advantage, 4 completion tokens each."""
    for skip in (True, False):
        batch = _batch_with_denominator(
            skip=skip, exclude=True, samples=_mixed_samples()
        )
        assert batch.num_loss_denominator_tokens == 2 * 4
        # Metrics and logs still see every consumed token.
        assert batch.num_global_valid_tokens == 8 * 4


def test_zero_advantage_token_frac_metric():
    batch = _batch_with_denominator(
        skip=True, exclude=True, samples=_mixed_samples()
    )
    (frac,) = [
        metric.value.value
        for metric in batch.metrics
        if metric.key == "train_batch/zero_advantage_token_frac"
    ]
    assert frac == 0.75


def test_excluding_zero_advantage_tokens_matches_a_batch_without_them():
    """The point of the switch: the zero-variance share stops scaling the update.

    Dividing by signal tokens only must give the same loss and gradient as a batch
    that never contained the zero-advantage samples at all, i.e. as if those groups
    had been dropped, whatever share of the batch they were.
    """
    vocab_size = 32
    loss_fn = DPPOLoss.Config().build()
    signal = [
        _sample(advantage=0.875, token=11),
        _sample(advantage=-0.125, token=12),
    ]

    def loss_and_grad(batch) -> tuple[float, torch.Tensor]:
        weight = torch.full((vocab_size, vocab_size), 0.1)
        weight.requires_grad_(True)
        total_loss = torch.zeros(())
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
                total_loss += loss.detach()
        return float(total_loss), weight.grad

    diluted_loss, diluted_grad = loss_and_grad(
        _batch_with_denominator(skip=True, exclude=True, samples=_mixed_samples())
    )
    clean_loss, clean_grad = loss_and_grad(
        _batch_with_denominator(skip=False, exclude=False, samples=signal)
    )
    torch.testing.assert_close(diluted_loss, clean_loss, rtol=1e-6, atol=1e-8)
    torch.testing.assert_close(diluted_grad, clean_grad, rtol=1e-6, atol=1e-8)
    assert clean_grad.abs().sum() > 0.0

    # And under the default the same batch is scaled down by the zero share (2 of 8).
    _, default_grad = loss_and_grad(_batch(skip=True, samples=_mixed_samples()))
    torch.testing.assert_close(default_grad * 4, clean_grad, rtol=1e-6, atol=1e-8)
