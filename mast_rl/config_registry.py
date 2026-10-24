# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""MAST config registry for multi-host SWE-R2E coding-agent RL experiments.

Reachable by the MAST launcher via ``--module mast_rl --config <fn>``. Each recipe
wraps a base SWE-R2E ``Controller.Config`` (async RL loop, from the OSS example
registry) into a ``MastRLConfig`` carrying the ``MastLauncher.Config`` the launcher
needs, and points ``hf_assets_path`` at the Qwen3 checkpoint staged on the mounted
Manifold bucket (``/mnt/torchtrain_datasets``).

Override launcher knobs on the CLI with ``--launcher.<field>`` and the generator
count with ``--num_generators N`` (each generator becomes its own MAST role). The
trainer host count follows from ``trainer.parallelism.data_parallel_shard_degree``
(FSDP-16 = 2 hosts, FSDP-24 = 3 hosts at 8 GPUs/host).
"""

from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import dataclass, field, fields

# ``mast`` resolves to ``mast_rl/mast.py``. The MAST entry point puts this dir on
# sys.path, but subprocesses that cloudpickle-import this config module (e.g. the
# RolloutWorker actor procs, which unpickle a MastRLConfig) may not -- so add this
# dir explicitly here, before the import, to keep ``import mast`` resolvable there.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mast import MastLauncher
from torchtitan.experiments.rl.controller import Controller
from torchtitan.experiments.rl.examples.tmax.policy_canary_controller import PolicyCanaryController

# Qwen3 checkpoints staged on the Manifold bucket (mounted at /mnt/<bucket> by run.sh).
_QWEN3_1_7B = "/mnt/torchtrain_datasets/tree/qwen3/Qwen3-1.7B"
_QWEN3_4B = "/mnt/torchtrain_datasets/tree/yichuan/qwen/Qwen3-4B"
_QWEN3_4B_BASE = "/mnt/torchtrain_datasets/tree/yichuan/qwen/Qwen3-4B-Base"
_QWEN3_8B = "/mnt/torchtrain_datasets/tree/yichuan/qwen/Qwen3-8B"
_QWEN3_32B = "/mnt/torchtrain_datasets/tree/qwen3/Qwen3-32B"
_QWEN3_5_27B = "/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.6-27B"
# Qwen3.5-27B: same GDN text tower as 3.6-27B (identical text_config dims and
# an identical 1199-tensor checkpoint layout, vision tower included), so it
# loads through the same path.
_QWEN3_5_27B_35 = "/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.5-27B"
# TMax-9B RL warm-start (hamishivi/Qwen3.5-9B, GDN-hybrid), staged to the bucket.
# Text-only for RL: the vision tower in the HF ckpt is dropped by the loader.
_QWEN3_5_9B = "/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.5-9B"
# Official base Qwen/Qwen3.5-9B (instruct, GDN-hybrid) for Search-R1: a general
# instruct start gives a clean held-out-EM curve, directly comparable to the dense
# Qwen3-8B Search-R1 run (the tmax path uses the terminal-SFT warm-start instead).
_QWEN3_5_9B_BASE = "/mnt/torchtrain_datasets/tree/yichuan/qwen3_5/Qwen3.5-9B"
_QWEN3_5_35B_A3B_BASE = (
    "/mnt/torchtrain_datasets/tree/yichuan/qwen3_5/Qwen3.5-35B-A3B-Base"
)
_QWEN3_5_35B_MATH_PROJECT = "qwen35-35b-a3b-math-20260729"

# H100 grandteton (msl_infra_pytorch_dev): its grandteton H100 quota lives in the
# ``_GENAI_PCI`` bucket, reached via region ``pci`` (the eag bucket is Quota 0).
_H100_PCI = MastLauncher.Config(region="pci")


@dataclass(kw_only=True, slots=True)
class MastRLConfig(Controller.Config):
    """``Controller.Config`` (async RL loop) extended with the MAST launcher."""

    launcher: MastLauncher.Config = field(default_factory=MastLauncher.Config)


@dataclass(kw_only=True, slots=True)
class MastPolicyCanaryConfig(PolicyCanaryController.Config):
    """Preserve the TMax admission controller when attaching the local MAST launcher."""

    launcher: MastLauncher.Config = field(default_factory=MastLauncher.Config)


def _with_launcher(
    rl_config: Controller.Config,
    *,
    hf_assets_path: str,
    launcher: MastLauncher.Config | None = None,
) -> MastRLConfig | MastPolicyCanaryConfig:
    """Wrap a ``Controller.Config`` into a ``MastRLConfig`` for the MAST launcher.

    Repoints ``hf_assets_path`` at the Manifold-mounted checkpoint and attaches a
    ``MastLauncher.Config`` (defaults if none given).
    """
    rl_config.hf_assets_path = hf_assets_path
    config_type = MastPolicyCanaryConfig if isinstance(rl_config, PolicyCanaryController.Config) else MastRLConfig
    return config_type(
        **{f.name: getattr(rl_config, f.name) for f in fields(rl_config)},
        launcher=launcher or MastLauncher.Config(),
    )


def rl_grpo_qwen3_1_7b_swe_r2e() -> MastRLConfig:
    """Qwen3-1.7B SWE-R2E smoke: 1 controller + 1 trainer + 1 generator (3 hosts).

    Smallest end-to-end check of the multi-host SWE launcher + Daytona path.
    """
    from torchtitan.experiments.rl.examples.swe_r2e.config_registry import (
        rl_grpo_qwen3_1_7b_swe_r2e as _swe,
    )

    return _with_launcher(_swe(), hf_assets_path=_QWEN3_1_7B, launcher=_H100_PCI)


def rl_grpo_qwen3_8b_swe_r2e() -> MastRLConfig:
    """Qwen3-8B SWE-R2E smoke: trainer TP=4 + 1 generator TP=2 + controller."""
    from torchtitan.experiments.rl.examples.swe_r2e.config_registry import (
        rl_grpo_qwen3_8b_swe_r2e as _swe,
    )

    return _with_launcher(_swe(), hf_assets_path=_QWEN3_8B, launcher=_H100_PCI)


def rl_grpo_qwen3_32b_swe_r2e() -> MastRLConfig:
    """Qwen3-32B SWE-R2E FSDP-8 baseline (sync): 1 trainer + 1 generator host.

    Single-host FSDP-8 trainer (bf16 master + bf16 Adam + FullAC) + TP-8 generator;
    with ``--num_generators 1`` -> 1 controller + 1 trainer + 1 generator = 3 hosts.
    Useful baseline; for overlapped collection + a wider FSDP shard use ``_fsdp16``.
    """
    from torchtitan.experiments.rl.examples.swe_r2e.config_registry import (
        rl_grpo_qwen3_32b_swe_r2e as _swe,
    )

    return _with_launcher(_swe(), hf_assets_path=_QWEN3_32B, launcher=_H100_PCI)


def rl_grpo_qwen3_32b_swe_r2e_fsdp16() -> MastRLConfig:
    """Qwen3-32B SWE-R2E async, FSDP-16 trainer + 3 TP-8 generators (6 H100 hosts).

    1 controller + 2 trainer (data_parallel_shard_degree=16) + 3 generator (TP=8).
    fp32 master + FullAC + chunked loss; rollout collection overlaps training by up
    to max_offpolicy_steps=2 so each step is short (fwd/bwd + CPU-staged weight
    sync) rather than blocking on the slow Claude Code rollouts. The launcher
    derives the 2 trainer hosts from dp_shard=16. Submit with NCCL_TRANSPORT=IB
    (run.sh wires NCCL_NET/IB) + the new torchstore (fast CPU-staged weight sync).
    """
    from torchtitan.experiments.rl.examples.swe_r2e.config_registry import (
        rl_grpo_qwen3_32b_swe_r2e_fsdp16 as _swe,
    )

    return _with_launcher(_swe(), hf_assets_path=_QWEN3_32B, launcher=_H100_PCI)


def rl_grpo_qwen3_32b_swe_r2e_fsdp24() -> MastRLConfig:
    """Qwen3-32B SWE-R2E async, FSDP-24 trainer + 3 TP-8 generators (7 H100 hosts).

    1 controller + 3 trainer (data_parallel_shard_degree=24) + 3 generator (TP=8).
    Same async overlap as ``_fsdp16`` with a wider trainer shard (more memory
    headroom / faster per-host fwd-bwd).
    """
    from torchtitan.experiments.rl.examples.swe_r2e.config_registry import (
        rl_grpo_qwen3_32b_swe_r2e_fsdp24 as _swe,
    )

    return _with_launcher(_swe(), hf_assets_path=_QWEN3_32B, launcher=_H100_PCI)


def rl_grpo_qwen3_5_27b_swe_r2e() -> MastRLConfig:
    """Qwen3.6-27B (Gated DeltaNet hybrid) SWE-R2E on a single 8-GPU host: trainer
    FSDP-4 + vLLM-native GDN generator TP-4 + controller. host_loop, off-policy 2,
    drop_zero_std False, 8x8. Submit via submit_swe_gdn.sh (SWE_GDN=1 makes run.sh
    switch CUDA_HOME to the pip cu13 ptxas 13.2 for the FLA/triton GDN kernels)."""
    from torchtitan.experiments.rl.examples.swe_r2e.config_registry import (
        rl_grpo_qwen3_5_27b_swe_r2e as _swe,
    )

    # region="pci": msl_infra_pytorch_dev (the default rm_attribution) has 96 hosts
    # of Grand Teton H100 on the ..._GENAI_PCI machine type (region "pci"), vs only
    # 4 on the non-PCI eag machine type under msl_infra_hw_enab_agentrl. The larger
    # PCI pool lets us run many more generators (rollout throughput bound).
    return _with_launcher(
        _swe(), hf_assets_path=_QWEN3_5_27B, launcher=MastLauncher.Config(region="pci")
    )


def rl_grpo_qwen3_5_27b_tmax() -> MastRLConfig:
    """Qwen3.6-27B (Gated DeltaNet hybrid) AI2 tmax terminal-agent on a single
    8-GPU host: same recipe as ``rl_grpo_qwen3_5_27b_swe_r2e`` (trainer FSDP-8 +
    vLLM-native GDN generator TP-4 + controller, host_loop, off-policy 2,
    drop_zero_std False, 8x8) with the rollouter swapped to ``TMaxRollouter`` +
    ``TMaxDataset``. Submit via submit_swe_tmax.sh (SWE_GDN=1 switches CUDA_HOME to
    the pip cu13 ptxas 13.2 for the FLA/triton GDN kernels; SWE_PROMPT_DATA points
    at the tmax JSONL)."""
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_27b_tmax as _tmax,
    )

    return _with_launcher(
        _tmax(), hf_assets_path=_QWEN3_5_27B, launcher=MastLauncher.Config(region="pci")
    )


def rl_grpo_qwen3_5_27b_tmax_fsdp16() -> MastRLConfig:
    """Qwen3.5-27B tmax terminal-agent at the full 65536 context.

    Topology: 1 controller + 2 trainer hosts (FSDP-16, derived from
    data_parallel_shard_degree) + N generator hosts (vLLM-native GDN, DP-8 x TP-1).
    Submit via rts_work/submit_terminus_27b_smoke.sh."""
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_27b_tmax_fsdp16 as _tmax16,
    )

    return _with_launcher(
        _tmax16(),
        hf_assets_path=_QWEN3_5_27B_35,
        launcher=MastLauncher.Config(region="pci"),
    )


def rl_grpo_qwen3_5_27b_tmax_fsdp32_tp2() -> MastRLConfig:
    """Qwen3.5-27B tmax terminal-agent on H100, scaled from the tuned 9B recipe.

    Topology: 1 controller + 8 trainer hosts (FSDP-32 x TP-2 = world 64) + N generator hosts (vLLM-native GDN, DP-4 x TP-2)
    + 1 eval generator. Submit via rts_work/submit_terminus_27b_fsdp32_tp2.sh."""
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_27b_tmax_fsdp32_tp2 as _tmax32tp2,
    )

    return _with_launcher(
        _tmax32tp2(),
        hf_assets_path=_QWEN3_5_27B_35,
        launcher=MastLauncher.Config(region="pci"),
    )


def rl_grpo_qwen3_5_27b_tmax_gb200() -> MastRLConfig:
    """Qwen3.5-27B tmax terminal-agent on MAST **GB200** (Grace ARM64, 2 GPU/host).

    12 hosts: 1 controller + 4 trainer (FSDP-8, derived from dp_shard / 2 GPUs per
    host) + 7 generator (TP-1 -> 2 engines each). ~186GB per GPU is what makes
    generator TP-1 possible at the 65536 context: the same TP-1 on an 80GB H100
    measured "Available KV cache memory: -8.96 GiB" and died at engine init.

    REQUIRES the aarch64 conda env; submit FROM the aarch64 host.
    """
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_27b_tmax_gb200 as _tmax_gb200,
    )

    return _with_launcher(
        _tmax_gb200(),
        hf_assets_path="/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.5-27B",
        launcher=MastLauncher.Config(
            host_type="gb200",
            region=os.environ.get("SWE_MAST_REGION", "lco"),
            rm_attribution="msl_infra_hw_enab_agentrl",
        ),
    )


def rl_grpo_qwen3_5_9b_search_r1() -> MastRLConfig:
    """Qwen3.5-9B (Gated DeltaNet hybrid) Search-R1 retrieval-QA RL on MAST.

    Validates the qwen3_5 linear-attention (GatedDeltaNet) implementation via the
    clean Search-R1 held-out EM curve, using the dense Search-R1 recipe unchanged
    (only the model + GDN toolchain differ). Topology: 1 controller host (brings up
    the dense-retrieval server) + 1 trainer host (FSDP-8) + N generator hosts
    (vLLM-native GDN, DP-8 x TP-1). Scale generators with ``--num_generators N``.

    Submit via submit_search_r1_gdn.sh: it sets SWE_GDN=1 (run.sh switches CUDA_HOME
    to the pip cu13 ptxas 13.2 for the FLA/triton GDN kernels) and leaves SWE_RUN
    unset (so run.sh takes the retriever branch, not the Daytona/Claude-Code one).
    """
    from torchtitan.experiments.rl.examples.search_r1.config_registry import (
        rl_grpo_qwen3_5_9b_search_r1 as _sr1,
    )

    return _with_launcher(
        _sr1(),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci"),
    )


def _qwen3_5_9b_dapo_math(
    *,
    batch_invariant: bool,
    max_response_tokens: int,
    unified_generator: bool = False,
    vllm_native_salt: bool = False,
) -> Controller.Config:
    """Adapt a Qwen3 DAPO-Math workload to Qwen3.5-9B on MAST."""
    from torchtitan.config import CompileConfig
    from torchtitan.distributed.activation_checkpoint import FullAC
    from torchtitan.experiments.rl.actors.generator import VLLMCudagraphConfig
    from torchtitan.experiments.rl.examples.dapo_math.config_registry import (
        rl_dapo_qwen3_4b_math_32k,
        rl_dapo_qwen3_4b_math_8k,
    )
    from torchtitan.experiments.rl.examples.search_r1.config_registry import (
        _set_max_seq_len,
    )
    from torchtitan.experiments.rl.models.cast_linear import LMHeadCastConverter
    from torchtitan.experiments.rl.renderer import RendererConfig
    from torchtitan.models.qwen3_5 import model_registry as qwen3_5_model_registry

    config_fns = {
        8192: rl_dapo_qwen3_4b_math_8k,
        32768: rl_dapo_qwen3_4b_math_32k,
    }
    try:
        config = config_fns[max_response_tokens]()
    except KeyError:
        raise ValueError(
            f"unsupported Qwen3.5 DAPO max_response_tokens={max_response_tokens}; "
            f"expected one of {sorted(config_fns)}"
        ) from None
    config.model_spec = qwen3_5_model_registry(
        "9B",
        attn_backend="varlen",
        converters=[LMHeadCastConverter.Config()],
    )
    _set_max_seq_len(config.model_spec, config.async_loop.batcher.batch.seq_len)
    config.compile = CompileConfig(enable=False, backend="aot_eager")
    config.renderer = RendererConfig(name="qwen3.5", enable_thinking=True)
    config.num_generators = 1
    # The 8K jobs run the async rollouter in the controller process so the
    # trainer can share that host. Spawning the 32-worker pool alongside eight
    # FSDP ranks can exceed Monarch's 30-second proc-mesh startup deadline.
    # The 32K jobs retain the separate controller host and worker pool.
    config.num_rollout_workers = 0 if max_response_tokens == 8192 else 32
    config.async_loop = dataclasses.replace(
        config.async_loop,
        validation=dataclasses.replace(config.async_loop.validation, interval=10),
    )
    config.trainer = dataclasses.replace(
        config.trainer,
        ac_config=FullAC.Config(),
        parallelism=dataclasses.replace(
            config.trainer.parallelism,
            data_parallel_shard_degree=8,
            tensor_parallel_degree=1,
            enable_sequence_parallel=False,
        ),
        loss=dataclasses.replace(config.trainer.loss, num_chunks=16),
    )
    config.generator = dataclasses.replace(
        config.generator,
        backend="vllm_native",
        gpu_memory_limit=0.6,
        vllm_additional_config={"gdn_prefill_backend": "triton"},
        cudagraph=VLLMCudagraphConfig(enable=True, mode="FULL_DECODE_ONLY"),
        enable_prefix_caching=True,
        parallelism=dataclasses.replace(
            config.generator.parallelism,
            data_parallel_degree=8,
            tensor_parallel_degree=1,
        ),
    )

    if batch_invariant or unified_generator:
        trainer_debug = dataclasses.replace(
            config.trainer.debug,
            seed=42,
            batch_invariant=batch_invariant,
            deterministic=batch_invariant,
        )
        generator_debug = dataclasses.replace(
            config.generator.debug,
            seed=42,
            batch_invariant=batch_invariant,
            deterministic=batch_invariant,
        )
        config.trainer = dataclasses.replace(
            config.trainer,
            debug=trainer_debug,
            # Keep FP32 master parameters and optimizer states so the 1e-6 AdamW
            # updates are not rounded away. FSDP casts forward parameters to BF16,
            # preserving the trainer/generator compute contract in both controls.
            training=dataclasses.replace(
                config.trainer.training,
                dtype="float32",
                mixed_precision_param="bfloat16",
                mixed_precision_reduce="float32",
            ),
        )
        config.generator = dataclasses.replace(
            config.generator,
            backend="torchtitan_wrapper",
            vllm_additional_config={},
            gdn_trainer_parity=batch_invariant,
            mamba_ssm_cache_dtype="float32",
            debug=generator_debug,
            enable_prefix_caching=True,
            cudagraph=VLLMCudagraphConfig(enable=True, mode="FULL_DECODE_ONLY"),
            salt_prefix_cache_on_weight_sync=True,
            reset_prefix_cache_on_weight_sync=False,
            reset_running_requests_on_weight_sync=False,
        )
    elif vllm_native_salt:
        config.trainer = dataclasses.replace(
            config.trainer,
            debug=dataclasses.replace(
                config.trainer.debug,
                seed=42,
                batch_invariant=False,
                deterministic=False,
            ),
            training=dataclasses.replace(
                config.trainer.training,
                dtype="float32",
                mixed_precision_param="bfloat16",
                mixed_precision_reduce="float32",
            ),
        )
        config.generator = dataclasses.replace(
            config.generator,
            debug=dataclasses.replace(
                config.generator.debug,
                seed=42,
                batch_invariant=False,
                deterministic=False,
            ),
            mamba_ssm_cache_dtype="float32",
            enable_prefix_caching=True,
            salt_prefix_cache_on_weight_sync=True,
            reset_prefix_cache_on_weight_sync=False,
            reset_running_requests_on_weight_sync=False,
        )
    return config


def _qwen3_5_35b_a3b_dapo_math(
    *,
    batch_invariant: bool,
    unified_generator: bool = False,
    vllm_native_salt: bool = False,
    smoke: bool = False,
) -> Controller.Config:
    """Build matched Qwen3.5-35B-A3B DAPO-Math comparison arms."""
    from torchtitan.experiments.rl.actors.generator import VLLMCudagraphConfig
    from torchtitan.experiments.rl.examples.search_r1.config_registry import (
        _set_max_seq_len,
    )
    from torchtitan.experiments.rl.models.cast_linear import LMHeadCastConverter
    from torchtitan.models.qwen3_5 import model_registry as qwen3_5_model_registry

    config = _qwen3_5_9b_dapo_math(
        batch_invariant=batch_invariant,
        max_response_tokens=8192,
        unified_generator=unified_generator,
        vllm_native_salt=vllm_native_salt,
    )
    config.model_spec = qwen3_5_model_registry(
        "35B-A3B",
        attn_backend="varlen",
        moe_comm_backend="standard",
        converters=[LMHeadCastConverter.Config()],
    )
    _set_max_seq_len(config.model_spec, config.async_loop.batcher.batch.seq_len)
    config.num_generators = 4
    config.num_rollout_workers = 0
    config.trainer = dataclasses.replace(
        config.trainer,
        parallelism=dataclasses.replace(
            config.trainer.parallelism,
            data_parallel_replicate_degree=1,
            data_parallel_shard_degree=16,
            tensor_parallel_degree=1,
            expert_parallel_degree=1,
            enable_sequence_parallel=False,
        ),
    )
    config.generator = dataclasses.replace(
        config.generator,
        # A complete BF16 rank is about 65.4 GiB before runtime state. Keep the
        # comparison free of EP while retaining headroom for the 640 MiB FP32
        # MoE combine temporary used by the 10,240-token vLLM warmup.
        gpu_memory_limit=0.95,
        parallelism=dataclasses.replace(
            config.generator.parallelism,
            data_parallel_degree=8,
            tensor_parallel_degree=1,
            expert_parallel_degree=1,
        ),
    )

    if smoke:
        smoke_max_total_tokens = 1024
        config.async_loop = dataclasses.replace(
            config.async_loop,
            num_training_steps=1,
            num_groups_per_train_step=1,
            group_size=16,
            max_offpolicy_steps=0,
            training_sample_builder=dataclasses.replace(
                config.async_loop.training_sample_builder,
                drop_zero_std_reward_groups=False,
            ),
            batcher=dataclasses.replace(
                config.async_loop.batcher,
                batch=dataclasses.replace(
                    config.async_loop.batcher.batch,
                    seq_len=smoke_max_total_tokens,
                ),
            ),
            validation=dataclasses.replace(
                config.async_loop.validation,
                num_samples=1,
                interval=1,
            ),
        )
        config.rollouter = dataclasses.replace(
            config.rollouter,
            validation_dataset=dataclasses.replace(
                config.rollouter.validation_dataset,
                num_samples=1,
            ),
            token_env=dataclasses.replace(
                config.rollouter.token_env,
                max_rollout_tokens=smoke_max_total_tokens,
            ),
        )
        config.trainer = dataclasses.replace(
            config.trainer,
            checkpoint=dataclasses.replace(
                config.trainer.checkpoint,
                load_only=True,
            ),
        )
        config.generator = dataclasses.replace(
            config.generator,
            sampling=dataclasses.replace(
                config.generator.sampling,
                max_tokens=256,
            ),
            cudagraph=VLLMCudagraphConfig(enable=False),
        )
        _set_max_seq_len(config.model_spec, smoke_max_total_tokens)

    return config


def _qwen3_5_35b_math_launcher(run_name: str) -> MastLauncher.Config:
    return MastLauncher.Config(
        region="pci",
        job_name_prefix=run_name,
        wandb_project=_QWEN3_5_35B_MATH_PROJECT,
    )


def rl_dapo_qwen3_4b_math_32k() -> MastRLConfig:
    """Post-trained Qwen3-4B DAPO-Math 32K recipe on MAST."""
    from torchtitan.experiments.rl.examples.dapo_math.config_registry import (
        rl_dapo_qwen3_4b_math_32k as _dapo_math,
    )

    return _with_launcher(
        _dapo_math(),
        hf_assets_path=_QWEN3_4B,
        launcher=MastLauncher.Config(region="pci", collocate_on_controller=True),
    )


def rl_dapo_qwen3_4b_base_math_8k() -> MastRLConfig:
    """Qwen3-4B-Base DAPO-Math 8K control recipe on MAST."""
    from torchtitan.experiments.rl.examples.dapo_math.config_registry import (
        rl_dapo_qwen3_4b_math_8k as _dapo_math,
    )

    return _with_launcher(
        _dapo_math(),
        hf_assets_path=_QWEN3_4B_BASE,
        launcher=MastLauncher.Config(region="pci", collocate_on_controller=True),
    )


def rl_dapo_qwen3_4b_base_math_8k_bi() -> MastRLConfig:
    """Qwen3-4B-Base DAPO-Math 8K batch-invariant control on MAST."""
    from torchtitan.experiments.rl.examples.dapo_math.config_registry import (
        rl_dapo_qwen3_4b_math_8k as _dapo_math,
    )

    config = _dapo_math()
    trainer_debug = dataclasses.replace(
        config.trainer.debug,
        seed=42,
        batch_invariant=True,
        deterministic=True,
    )
    generator_debug = dataclasses.replace(
        config.generator.debug,
        seed=42,
        batch_invariant=True,
        deterministic=True,
    )
    config.trainer = dataclasses.replace(
        config.trainer,
        debug=trainer_debug,
        training=dataclasses.replace(
            config.trainer.training,
            dtype="float32",
            mixed_precision_param="bfloat16",
            mixed_precision_reduce="float32",
        ),
        # The singleton FSDP axis applies BF16 mixed-precision parameters even
        # with TP2, retaining FP32 master parameters and optimizer states while
        # matching the original non-BI trainer topology.
        parallelism=dataclasses.replace(
            config.trainer.parallelism,
            data_parallel_shard_degree=1,
            tensor_parallel_degree=2,
            enable_sequence_parallel=False,
        ),
    )
    config.generator = dataclasses.replace(
        config.generator,
        backend="torchtitan_wrapper",
        vllm_additional_config={},
        debug=generator_debug,
        salt_prefix_cache_on_weight_sync=False,
        reset_prefix_cache_on_weight_sync=True,
        reset_running_requests_on_weight_sync=True,
    )
    config.metrics = dataclasses.replace(config.metrics, enable_tensorboard=True)
    return _with_launcher(
        config,
        hf_assets_path=_QWEN3_4B_BASE,
        launcher=MastLauncher.Config(region="pci", collocate_on_controller=True),
    )


def rl_dapo_qwen3_5_9b_math_32k() -> MastRLConfig:
    """Qwen3.5-9B DAPO-Math 32K async mixed-precision MAST run."""
    return _with_launcher(
        _qwen3_5_9b_dapo_math(batch_invariant=False, max_response_tokens=32768),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci"),
    )


def rl_dapo_qwen3_5_9b_math_32k_bi() -> MastRLConfig:
    """Qwen3.5-9B DAPO-Math 32K async batch-invariant MAST run."""
    return _with_launcher(
        _qwen3_5_9b_dapo_math(batch_invariant=True, max_response_tokens=32768),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci"),
    )


def rl_dapo_qwen3_5_9b_math_8k() -> MastRLConfig:
    """Qwen3.5-9B DAPO-Math 8K async mixed-precision MAST run."""
    return _with_launcher(
        _qwen3_5_9b_dapo_math(batch_invariant=False, max_response_tokens=8192),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci", trainer_on_controller=True),
    )


def rl_dapo_qwen3_5_9b_math_8k_bi() -> MastRLConfig:
    """Qwen3.5-9B DAPO-Math 8K async batch-invariant MAST run."""
    return _with_launcher(
        _qwen3_5_9b_dapo_math(batch_invariant=True, max_response_tokens=8192),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci", trainer_on_controller=True),
    )


def rl_dapo_qwen3_5_9b_math_8k_unified() -> MastRLConfig:
    """Qwen3.5-9B 8K non-BI control using the TorchTitan generator and salt KV."""
    return _with_launcher(
        _qwen3_5_9b_dapo_math(
            batch_invariant=False,
            max_response_tokens=8192,
            unified_generator=True,
        ),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci", trainer_on_controller=True),
    )


def rl_dapo_qwen3_5_9b_math_8k_vllm_salt() -> MastRLConfig:
    """Qwen3.5-9B 8K vLLM-native control using salted prefix-cache reuse."""
    return _with_launcher(
        _qwen3_5_9b_dapo_math(
            batch_invariant=False,
            max_response_tokens=8192,
            vllm_native_salt=True,
        ),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci", trainer_on_controller=True),
    )


def rl_dapo_qwen3_5_35b_a3b_math_smoke_unified() -> MastRLConfig:
    """One-step TorchTitan-wrapper smoke for Qwen3.5-35B-A3B Math."""
    return _with_launcher(
        _qwen3_5_35b_a3b_dapo_math(
            batch_invariant=False,
            unified_generator=True,
            smoke=True,
        ),
        hf_assets_path=_QWEN3_5_35B_A3B_BASE,
        launcher=_qwen3_5_35b_math_launcher("q35a3b-math-smoke-tt"),
    )


def rl_dapo_qwen3_5_35b_a3b_math_smoke_bi() -> MastRLConfig:
    """One-step batch-invariant TorchTitan-wrapper smoke for 35B-A3B Math."""
    return _with_launcher(
        _qwen3_5_35b_a3b_dapo_math(
            batch_invariant=True,
            smoke=True,
        ),
        hf_assets_path=_QWEN3_5_35B_A3B_BASE,
        launcher=_qwen3_5_35b_math_launcher("q35a3b-math-smoke-tt-bi"),
    )


def rl_dapo_qwen3_5_35b_a3b_math_smoke_vllm_salt() -> MastRLConfig:
    """One-step vLLM-native salted-cache smoke for 35B-A3B Math."""
    return _with_launcher(
        _qwen3_5_35b_a3b_dapo_math(
            batch_invariant=False,
            vllm_native_salt=True,
            smoke=True,
        ),
        hf_assets_path=_QWEN3_5_35B_A3B_BASE,
        launcher=_qwen3_5_35b_math_launcher("q35a3b-math-smoke-vllm"),
    )


def rl_dapo_qwen3_5_35b_a3b_math_8k_unified() -> MastRLConfig:
    """Qwen3.5-35B-A3B Math with the non-BI TorchTitan generator."""
    return _with_launcher(
        _qwen3_5_35b_a3b_dapo_math(
            batch_invariant=False,
            unified_generator=True,
        ),
        hf_assets_path=_QWEN3_5_35B_A3B_BASE,
        launcher=_qwen3_5_35b_math_launcher("q35a3b-math-tt"),
    )


def rl_dapo_qwen3_5_35b_a3b_math_8k_bi() -> MastRLConfig:
    """Qwen3.5-35B-A3B Math with the batch-invariant TorchTitan generator."""
    return _with_launcher(
        _qwen3_5_35b_a3b_dapo_math(batch_invariant=True),
        hf_assets_path=_QWEN3_5_35B_A3B_BASE,
        launcher=_qwen3_5_35b_math_launcher("q35a3b-math-tt-bi"),
    )


def rl_dapo_qwen3_5_35b_a3b_math_8k_vllm_salt() -> MastRLConfig:
    """Qwen3.5-35B-A3B Math with the vLLM-native salted-cache generator."""
    return _with_launcher(
        _qwen3_5_35b_a3b_dapo_math(
            batch_invariant=False,
            vllm_native_salt=True,
        ),
        hf_assets_path=_QWEN3_5_35B_A3B_BASE,
        launcher=_qwen3_5_35b_math_launcher("q35a3b-math-vllm"),
    )


def rl_grpo_qwen3_5_9b_search_r1_unified() -> MastRLConfig:
    """UNIFIED GDN generation variant of rl_grpo_qwen3_5_9b_search_r1 on MAST.

    Generation runs TorchTitan's OWN model for every layer
    (``backend="torchtitan_wrapper"``); the GDN layers borrow only vLLM's paged
    conv/ssm cache via ``VLLMGatedDeltaNetCore`` (params + fla math stay
    TorchTitan's). Everything else matches the native run, so the held-out EM
    curve is directly comparable. Submit via submit_search_r1_gdn.sh with
    SEARCH_R1_CONFIG=rl_grpo_qwen3_5_9b_search_r1_unified.
    """
    from torchtitan.experiments.rl.examples.search_r1.config_registry import (
        rl_grpo_qwen3_5_9b_search_r1_unified as _sr1u,
    )

    return _with_launcher(
        _sr1u(),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci"),
    )


def rl_grpo_qwen3_5_9b_search_r1_unified_prefix() -> MastRLConfig:
    """Diagnostic: unified GDN + prefix caching ON, on MAST.

    Discriminating run for the prefix-cache-path hypothesis: unified+prefix-OFF
    and native+prefix-ON are both clean, so a regression here isolates the custom
    core's GDN prefix-cache state handling. Submit via submit_search_r1_gdn.sh
    with SEARCH_R1_CONFIG=rl_grpo_qwen3_5_9b_search_r1_unified_prefix.
    """
    from torchtitan.experiments.rl.examples.search_r1.config_registry import (
        rl_grpo_qwen3_5_9b_search_r1_unified_prefix as _sr1up,
    )

    return _with_launcher(
        _sr1up(),
        hf_assets_path=_QWEN3_5_9B_BASE,
        launcher=MastLauncher.Config(region="pci"),
    )


def rl_grpo_qwen3_5_9b_tmax() -> MastRLConfig:
    """Qwen3.5-9B (Gated DeltaNet hybrid, text-only) AI2 tmax terminal-agent.

    The primary target: the paper's TMax-9B base. Trainer FSDP-8 + vLLM-native GDN
    generator TP-2 + controller, host_loop, off-policy 2, group_size 8, and
    drop_zero_std True (the paper's ``filter_zero_std_samples``). Scale the generator
    fleet with ``--num_generators N`` (paper uses 6; the PCI 96-quota lets us push
    12+). Submit via submit_swe_tmax.sh with SWE_CONFIG=rl_grpo_qwen3_5_9b_tmax
    (SWE_GDN=1 switches CUDA_HOME to the pip cu13 ptxas 13.2 for the FLA/triton GDN
    kernels; SWE_PROMPT_DATA points at the tmax JSONL)."""
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_9b_tmax as _tmax,
    )

    return _with_launcher(
        _tmax(), hf_assets_path=_QWEN3_5_9B, launcher=MastLauncher.Config(region="pci")
    )


def rl_grpo_qwen3_5_9b_tmax_tb2_eval() -> MastRLConfig:
    """Eval-only TMax-9B policy scoring on Terminal-Bench 2.0."""
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_9b_tmax_tb2_eval as _tmax_tb2_eval,
    )

    return _with_launcher(
        _tmax_tb2_eval(),
        hf_assets_path=_QWEN3_5_9B,
        launcher=MastLauncher.Config(region="pci"),
    )


def rl_grpo_qwen3_4b_tmax() -> MastRLConfig:
    """Qwen3-4B (DENSE, batch-invariant) tmax terminal-agent -- numerics control.

    Same tmax recipe/topology as ``rl_grpo_qwen3_5_9b_tmax`` (trainer FSDP-8 +
    torchtitan-wrapper generator DP-8 x TP-1 + controller, host_loop, group_size 32)
    but with the DENSE Qwen3-4B and batch_invariant + deterministic on both actors,
    so the trainer/generator per-token logprobs are bitwise-identical and
    ``bit_wise/logprob_diff/*`` should be ~0 (isolates the 9B GDN chunk-vs-recurrent
    drift). Submit via submit_swe_tmax_4b.sh (SWE_GDN toolchain + dense 4B weights)."""
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_4b_tmax as _tmax,
    )

    return _with_launcher(
        _tmax(), hf_assets_path=_QWEN3_4B, launcher=MastLauncher.Config(region="pci")
    )


# Nebius GB300 (region ofn, 4 GPUs/host): the msl_infra_pytorch_dct dedicated
# flexpool. Needs the aarch64 rlmast_arm env (build_conda_aarch64.sh).
def _nebius_gb300(**kwargs) -> MastLauncher.Config:
    return MastLauncher.Config(
        host_type="nebius_gb300",
        region="ofn",
        rm_attribution="msl_infra_pytorch_dct",
        enable_ttls=False,
        attach_via_mount=True,
        **kwargs,
    )


def rl_dapo_qwen3_4b_base_math_8k_nebius_smoke() -> MastRLConfig:
    """Qwen3-4B-Base DAPO-Math 8K, one Nebius GB300 host: TP-2 trainer + two
    TP-1 generators, three steps. Checks the env, mount, and egress on ofn."""
    from torchtitan.experiments.rl.examples.dapo_math.config_registry import (
        rl_dapo_qwen3_4b_math_8k as _dapo_math,
    )

    config = _dapo_math()
    config.num_generators = 2
    config.async_loop = dataclasses.replace(
        config.async_loop,
        num_training_steps=3,
        validation=dataclasses.replace(config.async_loop.validation, num_samples=0),
    )
    config.trainer = dataclasses.replace(
        config.trainer,
        checkpoint=dataclasses.replace(config.trainer.checkpoint, interval=1000),
    )
    return _with_launcher(
        config,
        hf_assets_path=_QWEN3_4B_BASE,
        launcher=_nebius_gb300(
            collocate_on_controller=True, job_name_prefix="dapo4b-nebius-smoke"
        ),
    )


def rl_grpo_qwen3_5_9b_tmax_nebius() -> MastRLConfig:
    """Qwen3.5-9B TMax on Nebius GB300: the trainer shares the controller host
    (FSDP over its 4 GPUs) and each generator host packs four DP-1 vLLM engines.

    The recipe is ``rl_grpo_qwen3_5_9b_tmax``; the B300 shape (independent DP-1
    engines, vllm_native + FLASH_ATTN, high rollout concurrency) is set by env in
    submit_tmax_9b_nebius.sh, which is the only supported entry point.
    """
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_9b_tmax as _tmax,
    )

    return _with_launcher(
        _tmax(),
        hf_assets_path=_QWEN3_5_9B,
        launcher=_nebius_gb300(
            trainer_on_controller=True, job_name_prefix="q35-9b-tmax-nebius"
        ),
    )
