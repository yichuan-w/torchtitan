# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""DPPO logs open-instruct ``policy/entropy_avg`` on rollout tokens."""

from __future__ import annotations

import torch

from torchtitan.experiments.rl.losses.dppo import DPPOLoss, entropy_from_logits


def test_entropy_from_logits_matches_open_instruct_formula():
    logits = torch.tensor([[[0.0, 0.0, 0.0], [10.0, -10.0, -10.0]]])
    entropy = entropy_from_logits(logits)
    uniform = float(torch.log(torch.tensor(3.0)))
    assert entropy is not None
    torch.testing.assert_close(entropy[0, 0], torch.tensor(uniform), atol=1e-5, rtol=1e-5)
    assert float(entropy[0, 1]) < 0.01


def test_dppo_logs_masked_policy_entropy_avg():
    loss_fn = DPPOLoss.Config().build()
    logits = torch.zeros(1, 4, 8)
    labels = torch.tensor([[0, 1, 2, 3]])
    generator_logprobs = torch.full((1, 4), -2.0)
    advantages = torch.ones(1, 4)
    loss_mask = torch.tensor([[False, True, True, False]])

    _, metrics = loss_fn(
        logits,
        labels,
        global_valid_tokens=2.0,
        generator_logprobs=generator_logprobs,
        advantages=advantages,
        loss_mask=loss_mask,
        metric_denominator=2.0,
    )

    expected = entropy_from_logits(logits)
    assert expected is not None
    want = (expected * loss_mask).sum() / 2.0
    torch.testing.assert_close(metrics["policy/entropy_avg"], want)
    assert float(metrics["policy/entropy_avg"]) > 0.0
