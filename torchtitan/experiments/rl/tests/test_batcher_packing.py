# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Batcher row packing: first-fit-decreasing vs next-fit."""

from __future__ import annotations

import random

import pytest
import torch

from torchtitan.experiments.rl.components.batcher import BatchConfig, Batcher
from torchtitan.experiments.rl.tests.test_batcher_prompt_mean import (
    _groups,
    _loss_and_grad,
    _SEQ_LEN,
)
from torchtitan.experiments.rl.types import RolloutTurnID, TrainingSample


def _batcher(packing: str, seq_len: int = 10) -> Batcher:
    return Batcher.Config(
        batch=BatchConfig(local_batch_size=1, seq_len=seq_len), packing=packing
    ).build(num_groups_per_train_step=1, dp_degree=1, pad_id=0, initial_policy_version=0)


def _sample(i: int, length: int) -> TrainingSample:
    # num_tokens_to_pack drops the last token, so length+1 raw tokens pack as length.
    return TrainingSample(
        min_policy_version=0,
        max_policy_version=0,
        rollout_id=RolloutTurnID(group_id=0, rollout_id=i, turn_id=0),
        token_ids=[1] * (length + 1),
        loss_mask=[True] * (length + 1),
        logprobs=[0.0] * (length + 1),
        advantage=[1.0] * (length + 1),
    )


def test_first_fit_decreasing_packs_tighter_than_next_fit() -> None:
    samples = [_sample(i, n) for i, n in enumerate([3, 6, 4, 7])]
    assert len(_batcher("next_fit")._assign_training_samples_to_rows(samples)) == 3
    rows = _batcher("first_fit_decreasing")._assign_training_samples_to_rows(samples)
    lengths = [[len(s.token_ids) - 1 for s in row] for row in rows]
    assert lengths == [[7, 3], [6, 4]]


def test_first_fit_decreasing_keeps_every_sample_once_within_seq_len() -> None:
    rng = random.Random(0)
    samples = [_sample(i, rng.randint(1, 60)) for i in range(300)]
    nf = _batcher("next_fit", 64)._assign_training_samples_to_rows(samples)
    ffd = _batcher("first_fit_decreasing", 64)._assign_training_samples_to_rows(samples)
    assert len(ffd) <= len(nf)
    assert sorted(s.rollout_id.rollout_id for row in ffd for s in row) == list(range(300))
    assert all(sum(len(s.token_ids) - 1 for s in row) <= 64 for row in ffd)


def test_unknown_packing_rejected() -> None:
    with pytest.raises(ValueError, match="unknown packing"):
        _batcher("best_fit")


@pytest.mark.parametrize("aggregation", ["token_mean", "prompt_mean"])
def test_packing_does_not_change_loss_or_grad(aggregation: str) -> None:
    def batch(packing: str):
        batcher = Batcher.Config(
            batch=BatchConfig(local_batch_size=2, seq_len=_SEQ_LEN),
            loss_aggregation=aggregation,
            packing=packing,
        ).build(
            num_groups_per_train_step=3, dp_degree=1, pad_id=0, initial_policy_version=0
        )
        out = None
        for group in _groups():
            out = batcher.add_training_samples(training_sample_group=group)
        assert out is not None
        return out

    loss_nf, grad_nf = _loss_and_grad(batch("next_fit"))
    loss_ffd, grad_ffd = _loss_and_grad(batch("first_fit_decreasing"))
    assert loss_ffd == pytest.approx(loss_nf, rel=1e-6)
    torch.testing.assert_close(grad_ffd, grad_nf, rtol=1e-6, atol=1e-7)


def test_spread_pad_token_ids_varies_only_the_row_tail_input_ids() -> None:
    batcher = Batcher.Config(
        batch=BatchConfig(local_batch_size=1, seq_len=10), spread_pad_token_ids=True
    ).build(num_groups_per_train_step=1, dp_degree=1, pad_id=7, initial_policy_version=0)
    row = batcher._pack_training_sample_row([_sample(0, 4)])
    assert row["input_ids"][0].tolist() == [1, 1, 1, 1, 0, 1, 2, 3, 4, 5]
    assert row["labels"][0, 4:].tolist() == [7] * 6
    assert not row["loss_mask"][0, 4:].any()
    empty = batcher._pack_training_sample_row([])
    assert len(set(empty["input_ids"][0].tolist())) == 10
