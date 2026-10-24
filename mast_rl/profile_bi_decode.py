#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Profile production-shaped Qwen3.5-9B batch-invariant vLLM decode.

Run with one H100, for example:

    CUDA_VISIBLE_DEVICES=2 torchrun --standalone --nproc-per-node=1 \
        mast_rl/profile_bi_decode.py --prompt-length 8000 --num-seqs 20

The default eager execution exposes individual CUDA kernels to Kineto. Pass
``--cudagraph`` to measure the production decode latency separately.
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import os
import time
from pathlib import Path

os.environ.setdefault("VLLM_USE_V2_MODEL_RUNNER", "0")
os.environ.setdefault("VLLM_ATTENTION_BACKEND", "CUSTOM")
os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")

import torch
import torch.distributed as dist
from vllm import EngineArgs, LLMEngine, SamplingParams
from vllm.config import AttentionConfig
from vllm.sampling_params import RequestOutputKind
from vllm.v1.attention.backends.registry import AttentionBackendEnum

from mast_rl.config_registry import rl_dapo_qwen3_5_9b_math_8k_bi
from torchtitan.components.checkpoint import CheckpointManager
from torchtitan.config import CommConfig
from torchtitan.distributed import utils as dist_utils
from torchtitan.distributed.utils import set_batch_invariance
from torchtitan.experiments.rl.actors.generator import (
    _install_flashinfer_allreduce_import_stub,
)
from torchtitan.experiments.rl.batch_invariance import (
    force_logprobs_fn_for_batch_invariance,
    patch_bmm_for_batch_invariance,
    patch_matmul_for_small_m_batch_invariance,
)
from torchtitan.experiments.rl.models.gdn_vllm_unified import (
    set_trainer_parity_fla,
)
from torchtitan.experiments.rl.models.cast_linear import (
    set_cast_linear_inference_cache,
)
from torchtitan.experiments.rl.models.vllm_registry import (
    register_to_vllm,
    TORCHTITAN_CONFIG_FORMAT,
)


DEFAULT_MODEL = (
    "/home/yichuan/.cache/huggingface/hub/models--Qwen--Qwen3.5-9B/"
    "snapshots/c202236235762e1c871ad0ccb60c8ee5ba337b9a"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt-length", type=int, default=8000)
    parser.add_argument("--num-seqs", type=int, default=20)
    parser.add_argument("--data-parallel-size", type=int, default=1)
    parser.add_argument("--warmup-steps", type=int, default=10)
    parser.add_argument("--profile-steps", type=int, default=10)
    parser.add_argument("--cudagraph", action="store_true")
    parser.add_argument("--disable-lm-head-cache", action="store_true")
    parser.add_argument(
        "--trace", default="/tmp/qwen35_9b_bi_decode_trace.json"
    )
    return parser.parse_args()


def build_engine(args: argparse.Namespace) -> LLMEngine:
    config = rl_dapo_qwen3_5_9b_math_8k_bi()
    generator = config.generator
    generator.parallelism = dataclasses.replace(
        generator.parallelism,
        data_parallel_degree=args.data_parallel_size,
        tensor_parallel_degree=1,
    )
    checkpoint = CheckpointManager.Config(
        enable=True,
        initial_load_in_hf=True,
        initial_load_path=args.model,
    )

    set_batch_invariance(True)
    patch_bmm_for_batch_invariance()
    force_logprobs_fn_for_batch_invariance()
    patch_matmul_for_small_m_batch_invariance()
    set_cast_linear_inference_cache(not args.disable_lm_head_cache)
    set_trainer_parity_fla(True)
    register_to_vllm(
        config.model_spec,
        parallelism=generator.parallelism,
        compile_config=config.compile,
        checkpoint_config=checkpoint,
        override=generator.override,
    )
    _install_flashinfer_allreduce_import_stub()

    engine_kwargs = dict(
        model=args.model,
        trust_remote_code=True,
        config_format=TORCHTITAN_CONFIG_FORMAT,
        dtype=generator.model_dtype,
        tensor_parallel_size=1,
        data_parallel_size=args.data_parallel_size,
        distributed_executor_backend="external_launcher",
        gpu_memory_utilization=0.85,
        enforce_eager=not args.cudagraph,
        disable_log_stats=True,
        mamba_cache_dtype=generator.mamba_cache_dtype,
        mamba_ssm_cache_dtype=generator.mamba_ssm_cache_dtype,
        max_model_len=config.model_spec.model.max_seq_len,
        max_num_seqs=args.num_seqs,
        max_num_batched_tokens=config.model_spec.model.max_seq_len,
        enable_prefix_caching=True,
        scheduling_policy="fcfs",
        disable_custom_all_reduce=True,
        attention_config=AttentionConfig(backend=AttentionBackendEnum.CUSTOM),
        seed=generator.debug.seed if generator.debug.seed is not None else 42,
    )
    if args.cudagraph:
        engine_kwargs["compilation_config"] = (
            generator.cudagraph.get_vllm_compilation_config(
                max_num_seqs=args.num_seqs
            )
        )
    return LLMEngine.from_engine_args(EngineArgs(**engine_kwargs))


def make_prompts(
    engine: LLMEngine, *, num_seqs: int, prompt_length: int
) -> list[list[int]]:
    tokenizer = engine.get_tokenizer()
    seed_ids = tokenizer.encode(
        "Solve the following mathematics problem carefully and show the reasoning. "
        "Let x be a real number and determine the requested value. "
    )
    if not seed_ids:
        raise RuntimeError("tokenizer returned an empty benchmark prompt")
    prompts = []
    for idx in range(num_seqs):
        repeats = (prompt_length + len(seed_ids) - 1) // len(seed_ids)
        prompt = (seed_ids * repeats)[:prompt_length]
        # Avoid turning the benchmark into a prefix-cache benchmark.
        prompt[-1] = seed_ids[idx % len(seed_ids)]
        prompts.append(prompt)
    return prompts


def main() -> None:
    args = parse_args()
    if args.warmup_steps < 10 or args.profile_steps < 10:
        raise ValueError("warmup-steps and profile-steps must both be at least 10")
    if not dist.is_initialized():
        dist_utils.init_distributed(CommConfig())
    if dist.get_world_size() != args.data_parallel_size:
        raise ValueError(
            f"world size {dist.get_world_size()} must equal data-parallel-size "
            f"{args.data_parallel_size}"
        )
    rank = dist.get_rank()

    engine = build_engine(args)
    prompts = make_prompts(
        engine, num_seqs=args.num_seqs, prompt_length=args.prompt_length
    )
    max_tokens = args.warmup_steps + args.profile_steps + args.num_seqs + 20
    sampling = SamplingParams(
        temperature=0.0,
        top_p=1.0,
        max_tokens=max_tokens,
        ignore_eos=True,
        logprobs=0,
        output_kind=RequestOutputKind.FINAL_ONLY,
    )
    for idx, prompt in enumerate(prompts):
        engine.add_request(
            f"profile-{rank}-{idx}", {"prompt_token_ids": prompt}, sampling
        )

    # Long prompts are admitted over several scheduler iterations. Run enough
    # iterations for the configured token budget to prefill every request, plus
    # two decode iterations, so measured M matches the production shard.
    max_batched_tokens = 10240
    prefill_steps = (
        math.ceil(args.num_seqs * args.prompt_length / max_batched_tokens) + 2
    )
    for _ in range(prefill_steps):
        engine.step()

    for _ in range(args.warmup_steps):
        engine.step()
    torch.cuda.synchronize()

    step_times_ms = []
    wall_times_ms = []
    activities = [
        torch.profiler.ProfilerActivity.CPU,
        torch.profiler.ProfilerActivity.CUDA,
    ]
    with torch.profiler.profile(activities=activities) as profile:
        for _ in range(args.profile_steps):
            start = torch.cuda.Event(enable_timing=True)
            end = torch.cuda.Event(enable_timing=True)
            wall_start = time.perf_counter()
            start.record()
            engine.step()
            end.record()
            end.synchronize()
            wall_times_ms.append((time.perf_counter() - wall_start) * 1000)
            step_times_ms.append(start.elapsed_time(end))
            profile.step()

    trace = Path(args.trace)
    if args.data_parallel_size > 1:
        trace = trace.with_name(f"{trace.stem}.rank{rank}{trace.suffix}")
    profile.export_chrome_trace(str(trace))
    mean_ms = sum(step_times_ms) / len(step_times_ms)
    mean_wall_ms = sum(wall_times_ms) / len(wall_times_ms)
    total_tps = args.num_seqs * 1000.0 / mean_ms
    if rank == 0:
        print(
            f"RESULT prompt_length={args.prompt_length} num_seqs={args.num_seqs} "
            f"data_parallel_size={args.data_parallel_size} "
            f"cudagraph={args.cudagraph} prefill_steps={prefill_steps} "
            f"mean_decode_step_ms={mean_ms:.3f} "
            f"mean_wall_step_ms={mean_wall_ms:.3f} "
            f"local_tokens_per_second={total_tps:.1f}"
        )
        print(
            profile.key_averages().table(
                sort_by="self_cuda_time_total", row_limit=40
            )
        )
        print(f"TRACE {trace}")

    engine.engine_core.shutdown()
    if dist.is_initialized():
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
