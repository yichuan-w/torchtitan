# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Score centering (arXiv 2609.20807): loss properties and top-k plumbing."""

from __future__ import annotations

import pytest
import torch

from torchtitan.experiments.rl.components.batcher import BatchConfig, Batcher
from torchtitan.experiments.rl.components.training_sample_builder import _concat_topk
from torchtitan.experiments.rl.losses.score_centering import ScoreCenteringLoss
from torchtitan.experiments.rl.types import RolloutTurnID, SamplerTopK, TrainingSample

_V = 12


def _grad(
    weight: str,
    trainer_logits: torch.Tensor,
    label: int,
    gen_logprob: float,
    topk_ids: torch.Tensor,
    topk_logprobs: torch.Tensor,
    advantage: float = 1.0,
) -> torch.Tensor:
    """d loss / d logits for one trained token."""
    loss_fn = ScoreCenteringLoss.Config(weight=weight).build()
    logits = trainer_logits.clone().view(1, 1, _V).requires_grad_(True)
    loss, _ = loss_fn(
        logits,
        torch.tensor([[label]]),
        1.0,
        generator_logprobs=torch.tensor([[gen_logprob]]),
        advantages=torch.tensor([[advantage]]),
        loss_mask=torch.tensor([[True]]),
        topk_token_ids=topk_ids.view(1, 1, -1).to(torch.int32),
        topk_logprobs=topk_logprobs.view(1, 1, -1),
    )
    loss.backward()
    return logits.grad.view(_V)


def _sampler_with_tail_like_trainer(p: torch.Tensor, k: int, seed: int) -> torch.Tensor:
    """A sampler distribution whose head differs from ``p`` but whose tail
    (outside its top-k) is proportional to ``p`` -- the paper's tail model, so
    top-k centering is exact."""
    gen = torch.Generator().manual_seed(seed)
    head = torch.randperm(_V, generator=gen)[:k]
    q = p.clone()
    q[head] = torch.rand(k, generator=gen, dtype=torch.float64) + 0.5
    tail = torch.ones(_V, dtype=torch.bool)
    tail[head] = False
    q[head] *= (1 - 0.1) / q[head].sum()
    q[tail] *= 0.1 / q[tail].sum()
    assert set(torch.topk(q, k).indices.tolist()) == set(head.tolist())
    return q


@pytest.mark.parametrize("weight", ["none", "tis", "mis"])
@pytest.mark.parametrize("k", [_V, 4])
def test_constant_advantage_has_zero_expected_update(weight: str, k: int) -> None:
    # The paper's central property: with a constant reward the expected update
    # under the sampler is zero (no drift), even though p != q.
    torch.manual_seed(0)
    p = torch.softmax(torch.randn(_V, dtype=torch.float64), -1)
    q = (
        torch.softmax(torch.randn(_V, dtype=torch.float64), -1)
        if k == _V
        else _sampler_with_tail_like_trainer(p, k, seed=1)
    )
    topk = torch.topk(q, k)
    expected = torch.zeros(_V, dtype=torch.float64)
    for y in range(_V):
        g = _grad(
            weight,
            p.log().float(),
            y,
            float(q[y].log()),
            topk.indices,
            topk.values.log().float(),
        )
        expected += q[y] * g.double()
    torch.testing.assert_close(expected, torch.zeros_like(expected), atol=2e-6, rtol=0)


def test_uncentered_constant_advantage_drifts() -> None:
    # Sanity check of the test above: plain PG (no top-k) does drift.
    torch.manual_seed(0)
    p = torch.softmax(torch.randn(_V, dtype=torch.float64), -1)
    q = torch.softmax(torch.randn(_V, dtype=torch.float64), -1)
    empty = torch.zeros(0)
    expected = sum(
        q[y] * _grad("none", p.log().float(), y, float(q[y].log()), empty, empty).double()
        for y in range(_V)
    )
    assert expected.abs().max() > 1e-2


def test_on_policy_is_plain_policy_gradient() -> None:
    # p == q: the centering coefficients vanish and SC is vanilla PG.
    torch.manual_seed(0)
    logits = torch.randn(_V)
    logp = torch.log_softmax(logits, -1)
    topk = torch.topk(logp, 5)
    sc = _grad("none", logits, 3, float(logp[3]), topk.indices, topk.values, 0.7)
    pg = _grad("none", logits, 3, float(logp[3]), torch.zeros(0), torch.zeros(0), 0.7)
    torch.testing.assert_close(sc, pg, atol=1e-6, rtol=0)


def test_rows_without_topk_keep_the_policy_gradient() -> None:
    torch.manual_seed(0)
    logits = torch.randn(_V)
    logp = torch.log_softmax(logits, -1)
    missing = SamplerTopK.empty_rows(1, 5)
    sc = _grad("none", logits, 3, float(logp[3]) - 0.5, missing.token_ids[0], missing.logprobs[0])
    pg = _grad("none", logits, 3, float(logp[3]) - 0.5, torch.zeros(0), torch.zeros(0))
    torch.testing.assert_close(sc, pg)


def test_rejects_unknown_weight() -> None:
    with pytest.raises(ValueError, match="weight"):
        ScoreCenteringLoss.Config(weight="ppo").build()


def _topk(rows: list[int], k: int = 2) -> SamplerTopK:
    ids = torch.tensor([[r] * k for r in rows], dtype=torch.int32)
    return SamplerTopK(token_ids=ids, logprobs=-ids.float())


def test_concat_topk_fills_prompt_and_env_segments() -> None:
    out = _concat_topk([(2, None), (3, _topk([10, 11, 12])), (1, None), (2, _topk([20, 21]))])
    assert out is not None
    assert out.token_ids[:, 0].tolist() == [0, 0, 10, 11, 12, 0, 20, 21]
    assert torch.isinf(out.logprobs[[0, 1, 5], 0]).all()
    assert _concat_topk([(2, None), (3, None)]) is None


def test_batcher_shifts_and_pads_topk_with_the_row() -> None:
    batcher = Batcher.Config(batch=BatchConfig(local_batch_size=2, seq_len=8)).build(
        num_groups_per_train_step=1, dp_degree=1, pad_id=0, initial_policy_version=0
    )
    sample = TrainingSample(
        min_policy_version=0,
        max_policy_version=0,
        rollout_id=RolloutTurnID(group_id=0, rollout_id=0, turn_id=0),
        token_ids=[1, 2, 3, 4],
        loss_mask=[False, False, True, True],
        logprobs=[0.0, 0.0, -0.1, -0.2],
        advantage=[0.0, 0.0, 1.0, 1.0],
        topk=_concat_topk([(2, None), (2, _topk([3, 4]))]),
    )
    rows = [batcher._pack_training_sample_row([sample]), batcher._pack_training_sample_row([])]
    mb = Batcher.collate(rows)
    assert mb.topk_token_ids is not None and mb.topk_logprobs is not None
    assert mb.topk_token_ids.shape == (2, 8, 2)
    # Shifted like labels: topk row t belongs to the prediction of labels[t].
    assert mb.labels[0, :3].tolist() == [2, 3, 4]
    assert mb.topk_token_ids[0, :3, 0].tolist() == [0, 3, 4]
    assert mb.loss_mask[0, :3].tolist() == [False, True, True]
    assert torch.isinf(mb.topk_logprobs[0, 3:]).all()
    assert torch.isinf(mb.topk_logprobs[1]).all()


def test_batcher_without_topk_is_unchanged() -> None:
    batcher = Batcher.Config(batch=BatchConfig(local_batch_size=1, seq_len=8)).build(
        num_groups_per_train_step=1, dp_degree=1, pad_id=0, initial_policy_version=0
    )
    mb = Batcher.collate([batcher._pack_training_sample_row([])])
    assert mb.topk_token_ids is None and mb.topk_logprobs is None


def test_split_flat_logprobs_takes_sampled_first_then_topk() -> None:
    pytest.importorskip("vllm")
    from vllm.logprobs import append_logprobs_for_next_position, FlatLogprobs

    from torchtitan.experiments.rl.actors.generator import _split_flat_logprobs

    flat = FlatLogprobs()
    # vLLM writes [sampled, top-1, top-2] per position; the sampled token may
    # also be in the top-k (position 0) or not (position 1).
    for ids, lps in [([7, 7, 3], [-0.1, -0.1, -2.5]), ([9, 7, 3], [-4.0, -0.3, -1.6])]:
        append_logprobs_for_next_position(
            flat, ids, lps, [None] * 3, rank=1, num_logprobs=2
        )
    sampled, topk = _split_flat_logprobs(flat)
    assert sampled == pytest.approx([-0.1, -4.0])
    assert topk.token_ids.tolist() == [[7, 3], [7, 3]]
    torch.testing.assert_close(topk.logprobs, torch.tensor([[-0.1, -2.5], [-0.3, -1.6]]))
