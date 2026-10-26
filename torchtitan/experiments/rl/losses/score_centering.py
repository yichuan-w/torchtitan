# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Score centering: policy gradient with the sampler's expected score subtracted.

Marek & Ryabinin, "Score Centering Stabilizes Off-policy Reinforcement Learning"
(https://arxiv.org/abs/2609.20807). When the sampler ``q`` (vLLM, possibly stale)
differs from the trainer ``p``, the expected update ``E_q[A s_y]`` splits into
``E_q[A] * sbar`` ("drift", a distillation pull toward the sampler that compounds
through weight sync) plus ``Cov_q(A, s_y)`` (the signal), where
``sbar = sum_v q_v grad log p_v``. Subtracting ``sbar`` from every score cancels
the drift exactly. With top-k sampler logprobs (head ``H``) and the trainer's
tail rescaled to the sampler's tail mass, the per-token loss is (paper Eq. 15)::

    L = -A * ( sg[w_y] log p_y - sum_{v in H} sg[q_v w_v - alpha p_v] log p_v )

``w = f(p/q)`` is an optional importance weight (``f = 1`` is plain score
centering; TIS / MIS compose additively), ``rho`` is the sampler-to-trainer tail
mass ratio and ``alpha = rho * f(1/rho)``. The paper recommends composing with
TIS or MIS when the sampler can be stale, which async RL always is.

There is deliberately no PPO clip and no DPPO mask here: both act on the sampled
token only, which would bring the drift back. Note that composing with the
UNCLIPPED ratio (``f(r) = r``, what DPPO uses) makes the centering term vanish
identically, so this is not a flag on DPPO.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch.distributed.tensor import DTensor, Partial, Replicate

from torchtitan.components.loss import BaseLoss
from torchtitan.config import CompileConfig
from torchtitan.experiments.rl.losses.dppo import entropy_from_logits

# Same overflow guard as DPPO on log(p/q) before exp().
_MAX_LOG_RATIO = 10.0
_WEIGHTS = ("none", "tis", "mis")


def _gather_logprobs(logits: torch.Tensor, ids: torch.Tensor) -> torch.Tensor:
    """Trainer log-probs of ``ids`` ``[B, S, n]`` under ``logits`` ``[B, S, V]``,
    differentiable w.r.t. ``logits``."""
    if isinstance(logits, DTensor):
        if any(isinstance(p, Partial) for p in logits.placements):
            raise ValueError(
                f"score centering expects non-partial logits, got {logits.placements}"
            )
        # A vocab-sharded (TP) lm_head output is gathered to the full vocab. Every
        # rank then computes the same loss, so the local gradient is Replicate and
        # the redistribute's backward slices it back to each rank's shard.
        replicate = [Replicate()] * logits.device_mesh.ndim
        logits = logits.redistribute(placements=replicate).to_local(
            grad_placements=replicate
        )
    logits = logits.float()
    lse = torch.logsumexp(logits, dim=-1, keepdim=True)
    return logits.gather(-1, ids) - lse


class ScoreCenteringLoss(BaseLoss):
    """Policy gradient with score centering, optionally composed with TIS / MIS.

    Needs the sampler's top-k logprobs for every trained token
    (``SamplingConfig.num_topk_logprobs``). A trained token without them (its
    turn was sampled without top-k) keeps the weighted policy-gradient term and
    loses only the centering; ``sc/topk_missing_frac`` counts these. As in DPPO,
    a token whose generator logprob is non-finite is dropped, and the summed
    per-token loss is divided by ``global_valid_tokens``.
    """

    # The trainer only passes top-k tensors to losses that take them.
    accepts_topk = True

    @dataclass(kw_only=True, slots=True)
    class Config(BaseLoss.Config):
        weight: str = "tis"
        """Importance weight composed under the centering: ``"none"`` (plain score
        centering), ``"tis"`` (``min(r, tis_cap)``) or ``"mis"`` (``r`` inside
        ``[mis_low, mis_high]``, else 0)."""

        tis_cap: float = 2.0
        mis_low: float = 0.5
        mis_high: float = 5.0

        tail_mass_eps: float = 1e-6
        """Floor on the sampler and trainer tail masses outside the top-k."""

    def __init__(
        self,
        config: Config,
        *,
        compile_config: CompileConfig | None = None,
    ) -> None:
        del compile_config
        if config.weight not in _WEIGHTS:
            raise ValueError(
                f"ScoreCenteringLoss.weight must be one of {_WEIGHTS}, got {config.weight!r}"
            )
        self.weight = config.weight
        self.tis_cap = config.tis_cap
        self.mis_low = config.mis_low
        self.mis_high = config.mis_high
        self.tail_mass_eps = config.tail_mass_eps

    def _weight_fn(self, ratio: torch.Tensor) -> torch.Tensor:
        if self.weight == "tis":
            return ratio.clamp(max=self.tis_cap)
        if self.weight == "mis":
            keep = (ratio >= self.mis_low) & (ratio <= self.mis_high)
            return torch.where(keep, ratio, torch.zeros_like(ratio))
        return torch.ones_like(ratio)

    def __call__(
        self,
        logits: torch.Tensor,
        labels: torch.Tensor,
        global_valid_tokens: float | None = None,
        *,
        generator_logprobs: torch.Tensor,
        advantages: torch.Tensor,
        loss_mask: torch.Tensor,
        topk_token_ids: torch.Tensor | None = None,
        topk_logprobs: torch.Tensor | None = None,
        positions: torch.Tensor | None = None,
        metric_denominator: float | None = None,
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        """Args mirror :class:`DPPOLoss`, plus the sampler top-k
        ``topk_token_ids`` / ``topk_logprobs`` ``[B, S, k]`` (``-inf`` logprobs
        on rows the sampler did not produce)."""
        del positions
        if topk_token_ids is None:
            # Pad-only microbatch, or no turn sampled with top-k.
            topk_token_ids = labels.new_zeros((*labels.shape, 0))
            topk_logprobs = generator_logprobs.new_zeros((*labels.shape, 0))
        assert topk_logprobs is not None
        ids = torch.cat([labels.unsqueeze(-1), topk_token_ids.long()], dim=-1)
        logp_all = _gather_logprobs(logits, ids)
        logp_y = logp_all[..., 0]
        logp_head = logp_all[..., 1:]

        raw_log_ratio = logp_y.detach() - generator_logprobs
        loss_mask = loss_mask & torch.isfinite(raw_log_ratio)

        with torch.no_grad():
            ratio_y = torch.exp(
                torch.nan_to_num(raw_log_ratio).clamp(-_MAX_LOG_RATIO, _MAX_LOG_RATIO)
            )
            w_y = self._weight_fn(ratio_y)

            has_topk = (
                torch.isfinite(topk_logprobs[..., 0])
                if topk_logprobs.shape[-1] > 0
                else torch.zeros_like(loss_mask)
            )
            q_head = torch.exp(topk_logprobs)  # -inf rows -> 0
            p_head = torch.exp(logp_head.detach())
            eps = self.tail_mass_eps
            samp_tail = (1.0 - q_head.sum(-1)).clamp(min=eps)
            train_tail = (1.0 - p_head.sum(-1)).clamp(min=eps)
            rho = samp_tail / train_tail
            alpha = rho * self._weight_fn(1.0 / rho)
            log_ratio_head = torch.nan_to_num(
                logp_head.detach() - topk_logprobs
            ).clamp(-_MAX_LOG_RATIO, _MAX_LOG_RATIO)
            w_head = self._weight_fn(torch.exp(log_ratio_head))
            coef_head = q_head * w_head - alpha.unsqueeze(-1) * p_head
            coef_head = torch.where(
                has_topk.unsqueeze(-1), coef_head, torch.zeros_like(coef_head)
            )

        token_objective = w_y * logp_y - (coef_head * logp_head).sum(-1)
        token_loss = torch.where(
            loss_mask, -(advantages * token_objective), torch.zeros_like(token_objective)
        )
        loss_denominator = (
            max(global_valid_tokens, 1) if global_valid_tokens is not None else 1
        )
        loss = token_loss.sum() / loss_denominator
        # Per-trained-token metrics divide by the actually-packed valid tokens
        # (see DPPOLoss for why this differs from the loss denominator).
        metric_denom = (
            max(metric_denominator, 1)
            if metric_denominator is not None
            else loss_denominator
        )

        with torch.no_grad():
            trained = loss_mask & has_topk
            diff = torch.where(loss_mask, raw_log_ratio, torch.zeros_like(raw_log_ratio))
            metrics = {
                "loss/mean": loss.detach(),
                "loss/ratio_mean": (ratio_y * loss_mask).sum() / metric_denom,
                "loss/is_weight_mean": (w_y * loss_mask).sum() / metric_denom,
                "sc/topk_missing_frac": (loss_mask & ~has_topk).float().sum()
                / metric_denom,
                # Sampler mass covered by the top-k (paper: > 99.9% at k=128).
                "sc/sampler_head_mass_mean": (q_head.sum(-1) * trained).sum()
                / metric_denom,
                "sc/tail_mass_ratio_mean": (rho * trained).sum() / metric_denom,
                # Size of the subtracted expected-score coefficients; ~0 when the
                # trainer matches the sampler.
                "sc/centering_coef_l1_mean": (coef_head.abs().sum(-1) * trained).sum()
                / metric_denom,
                "bit_wise/logprob_diff/abs_mean": diff.abs().sum() / metric_denom,
                "bit_wise/logprob_diff/max": diff.abs().max(),
                "debug/vllm_local_kl_k3_mean": (torch.exp(diff) - diff - 1.0).sum()
                / metric_denom,
            }
            if self.weight != "none":
                metrics["sc/is_weight_changed_frac"] = (
                    (w_y != ratio_y) & loss_mask
                ).float().sum() / metric_denom
            entropy = entropy_from_logits(logits)
            if entropy is not None:
                metrics["policy/entropy_avg"] = (entropy * loss_mask).sum() / metric_denom

        return loss, metrics
