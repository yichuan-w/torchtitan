# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Single-node Qwen3-4B-Base DAPO-Math recipes."""

from __future__ import annotations

import dataclasses

from torchtitan.components.checkpoint import CheckpointManager
from torchtitan.components.loss import ChunkedLossWrapper
from torchtitan.components.lr_scheduler import LRSchedulersContainer
from torchtitan.components.optimizer import default_adamw
from torchtitan.config import CompileConfig, ParallelismConfig, TrainingConfig
from torchtitan.experiments.rl.actors.generator import (
    SamplingConfig,
    VLLMCudagraphConfig,
    VLLMGenerator,
)
from torchtitan.experiments.rl.actors.trainer import PolicyTrainer
from torchtitan.experiments.rl.components.batcher import BatchConfig, Batcher
from torchtitan.experiments.rl.controller import (
    AsyncLoopConfig,
    Controller,
    ValidationConfig,
)
from torchtitan.experiments.rl.environment import TokenEnv
from torchtitan.experiments.rl.examples.dapo_math.data import AIME2025Dataset
from torchtitan.experiments.rl.examples.dapo_math.rollouter import DapoMathRollouter
from torchtitan.experiments.rl.losses import DAPOLoss, ScoreCenteringLoss
from torchtitan.experiments.rl.models.cast_linear import LMHeadCastConverter
from torchtitan.experiments.rl.models.vllm_registry import InferenceParallelismConfig
from torchtitan.experiments.rl.observability.metrics import MetricsProcessor
from torchtitan.experiments.rl.renderer import RendererConfig
from torchtitan.experiments.rl.routing.inter_generator_router import (
    InterGeneratorRouter,
)
from torchtitan.experiments.rl.routing.strategies import LeastLoadedRoutingStrategy
from torchtitan.models.qwen3 import model_registry


def _qwen3_4b_dapo_math_config(
    *,
    max_response_tokens: int,
    max_total_tokens: int,
    dump_folder: str,
) -> Controller.Config:
    """Build the shared Qwen3-4B DAPO-Math configuration."""
    num_validation_samples = 30
    validation_dataset = AIME2025Dataset.Config(
        num_samples=num_validation_samples,
    )
    return Controller.Config(
        model_spec=model_registry(
            "4B",
            attn_backend="varlen",
            # Compute vocabulary logits in fp32; the rest of the forward uses bf16.
            converters=[LMHeadCastConverter.Config()],
        ),
        hf_assets_path="torchtitan/experiments/rl/example_checkpoint/Qwen3-4B-Base",
        dump_folder=dump_folder,
        async_loop=AsyncLoopConfig(
            num_training_steps=150,
            num_groups_per_train_step=8,
            group_size=16,
            max_offpolicy_steps=4,
            validation=ValidationConfig(
                num_samples=num_validation_samples,
            ),
            batcher=Batcher.Config(
                batch=BatchConfig(local_batch_size=1, seq_len=max_total_tokens),
            ),
        ),
        compile=CompileConfig(enable=True, backend="aot_eager"),
        rollouter=DapoMathRollouter.Config(
            validation_dataset=validation_dataset,
            token_env=TokenEnv.Config(
                max_rollout_tokens=max_total_tokens,
                max_num_turns=1,
            ),
        ),
        renderer=RendererConfig(name="qwen3", enable_thinking=True),
        num_generators=6,
        generator_router=InterGeneratorRouter.Config(
            strategy=LeastLoadedRoutingStrategy.Config()
        ),
        metrics=MetricsProcessor.Config(
            enable_wandb=True,
            console_log_keys_validation=[
                "validation_reward/_mean",
                "validation_reward/_max",
                "validation/response_length/mean",
                "timing/validate",
            ],
        ),
        trainer=PolicyTrainer.Config(
            optimizer=default_adamw(
                lr=1e-6,
                betas=(0.9, 0.98),
                weight_decay=0.1,
            ),
            # A minimum factor of 1 keeps the learning rate constant.
            lr_scheduler=LRSchedulersContainer.Config(
                warmup_steps=0,
                min_lr_factor=1.0,
            ),
            training=TrainingConfig(),
            parallelism=ParallelismConfig(
                data_parallel_replicate_degree=1,
                data_parallel_shard_degree=1,
                tensor_parallel_degree=2,
            ),
            checkpoint=CheckpointManager.Config(
                enable=True,
                initial_load_in_hf=True,
                interval=100,
                last_save_model_only=False,
                keep_latest_k=3,
            ),
            loss=ChunkedLossWrapper.Config(
                num_chunks=8,
                loss_fn=DAPOLoss.Config(
                    ratio_clip_low=0.2,
                    ratio_clip_high=0.28,
                ),
            ),
        ),
        generator=VLLMGenerator.Config(
            model_dtype="bfloat16",
            parallelism=InferenceParallelismConfig(
                data_parallel_degree=1,
                tensor_parallel_degree=1,
            ),
            cudagraph=VLLMCudagraphConfig(enable=True),
            checkpoint=CheckpointManager.Config(enable=False),
            sampling=SamplingConfig(
                temperature=1.0,
                top_p=1.0,
                max_tokens=max_response_tokens,
            ),
        ),
    )


def rl_dapo_qwen3_4b_math_8k() -> Controller.Config:
    """Run 8K responses on one node: one TP=2 trainer and six TP=1 generators."""
    return _qwen3_4b_dapo_math_config(
        max_response_tokens=8192,
        max_total_tokens=10240,
        dump_folder="outputs/rl/qwen3_4b_dapo_math_8k",
    )


def rl_dapo_qwen3_4b_math_32k() -> Controller.Config:
    """Run 32K responses on one node: one TP=2 trainer and six TP=1 generators."""
    return _qwen3_4b_dapo_math_config(
        max_response_tokens=32768,
        max_total_tokens=34816,
        dump_folder="outputs/rl/qwen3_4b_dapo_math_32k",
    )


def _qwen3_4b_math_8k_stale(
    *, score_centering: bool, num_generators: int = 2
) -> Controller.Config:
    """TP=2 trainer with a deep off-policy window, for the score-centering A/B
    (two generators = half a node by default). Both arms train the same
    TIS-weighted policy gradient; the SC arm also asks the sampler for its top-k
    and subtracts the expected score (arXiv 2609.20807), so the centering term
    is the only difference."""
    arm = "sc_tis" if score_centering else "tis"
    config = _qwen3_4b_dapo_math_config(
        max_response_tokens=8192,
        max_total_tokens=10240,
        dump_folder=f"outputs/rl/qwen3_4b_math_8k_stale_{arm}",
    )
    config.num_generators = num_generators
    config.async_loop.max_offpolicy_steps = 8
    config.trainer.loss = dataclasses.replace(
        config.trainer.loss, loss_fn=ScoreCenteringLoss.Config(weight="tis")
    )
    if score_centering:
        config.generator.sampling = dataclasses.replace(
            config.generator.sampling, num_topk_logprobs=32
        )
    return config


def rl_qwen3_4b_math_8k_stale_tis() -> Controller.Config:
    """Score-centering A/B baseline: TIS policy gradient, off-policy window 8."""
    return _qwen3_4b_math_8k_stale(score_centering=False)


def rl_qwen3_4b_math_8k_stale_sc_tis() -> Controller.Config:
    """Score-centering A/B: TIS policy gradient plus score centering (k=32)."""
    return _qwen3_4b_math_8k_stale(score_centering=True)


def _qwen3_4b_math_8k_sc_ablate(*, score_centering: bool) -> Controller.Config:
    """Full-node score-centering ablation with a running eval: six generators,
    200 steps, AIME 2025 avg@8 at temperature 1 every 25 steps."""
    config = _qwen3_4b_math_8k_stale(score_centering=score_centering, num_generators=6)
    config.async_loop.num_training_steps = 200
    config.async_loop.validation = ValidationConfig(
        num_samples=30, interval=25, group_size=8, temperature=1.0
    )
    return config


def rl_qwen3_4b_math_8k_sc_ablate_tis() -> Controller.Config:
    """Score-centering ablation, TIS arm."""
    return _qwen3_4b_math_8k_sc_ablate(score_centering=False)


def rl_qwen3_4b_math_8k_sc_ablate_sc_tis() -> Controller.Config:
    """Score-centering ablation, SC + TIS arm."""
    return _qwen3_4b_math_8k_sc_ablate(score_centering=True)
