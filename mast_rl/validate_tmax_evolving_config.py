#!/usr/bin/env python3
"""Validate the resolved TorchTitan configuration for the evolving MAST run."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

from torchtitan.config import ConfigManager
from torchtitan.experiments.rl.controller import Controller
from torchtitan.experiments.rl.train import (
    _compute_generator_world_size,
    _compute_trainer_world_size,
)


def _expect(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def main() -> None:
    seed_profile = os.environ.get("TMAX_EVOLVE_SEED_PROFILE", "tmax-longlongcheck431")
    expected_simplify = "1" if seed_profile == "rebench-tmax-reaudit452" else "0"
    num_eval_generators = int(os.environ.get("SWE_NUM_EVAL_GENERATORS", "1"))
    num_eval_rollout_workers = int(os.environ.get("SWE_NUM_EVAL_ROLLOUT_WORKERS", "8"))
    eval_rollout_concurrency = int(os.environ.get("SWE_EVAL_ROLLOUT_CONCURRENCY", "64"))
    validation_hf_assets_path = os.environ.get(
        "TMAX_VALIDATE_HF_ASSETS_PATH",
        str(
            Path(os.environ.get("TMAX_LOCAL_ROOT") or Path.home() / "rl")
            / ".torchtrain_datasets/tree/qwen3_5/Qwen3.5-9B"
        ),
    )
    cfg = ConfigManager().parse_args(
        [
            "--module",
            "mast_rl",
            "--config",
            "rl_grpo_qwen3_5_9b_tmax",
            "--hf_assets_path",
            validation_hf_assets_path,
            "--num_generators",
            "8",
            "--launcher.host-type",
            "grandteton_80g_roce",
            "--launcher.region",
            "pci",
            "--launcher.wandb-team",
            os.environ.get("TMAX_WANDB_ENTITY")
            or os.environ.get("TMAX_OPERATOR")
            or getpass.getuser(),
            "--launcher.wandb-project",
            "torchtitan",
            "--launcher.max-retries",
            "10",
        ]
    )
    policy_canary_k = int(os.environ.get("SWE_POLICY_CANARY_K", "0"))
    if policy_canary_k:
        from torchtitan.experiments.rl.examples.tmax.policy_canary_controller import (
            PolicyCanaryController,
        )

        _expect("controller_class", cfg._owner, PolicyCanaryController)
        _expect("policy_canary_k", cfg.policy_canary.group_size, policy_canary_k)
        _expect("policy_canary_root", cfg.policy_canary_root, os.environ["TRL_BASE"])
        for name, env_name, default in (
            ("max_concurrency", "SWE_POLICY_CANARY_MAX_CONCURRENCY", 4),
            ("max_version_span", "SWE_POLICY_CANARY_MAX_VERSION_SPAN", 5),
            ("timeout_sec", "SWE_POLICY_CANARY_TIMEOUT_SEC", 7200),
            ("poll_sec", "SWE_POLICY_CANARY_POLL_SEC", 10),
        ):
            _expect(
                f"policy_canary_{name}",
                getattr(cfg.policy_canary, name),
                int(os.environ.get(env_name, str(default))),
            )
    else:
        _expect("controller_class", cfg._owner, Controller)
    trainer_world_size = _compute_trainer_world_size(cfg.trainer.parallelism)
    generator_world_size = _compute_generator_world_size(cfg.generator.parallelism)
    eval_generator_world_size = _compute_generator_world_size(
        cfg.eval_generator_parallelism()
    )
    if len(cfg.trainer.optimizer.param_groups) != 1:
        raise ValueError(
            "optimizer: expected one parameter group, got "
            f"{len(cfg.trainer.optimizer.param_groups)}"
        )
    optimizer_group = cfg.trainer.optimizer.param_groups[0]
    checks = {
        "trainer_world_size": (trainer_world_size, 24),
        "validation_hf_assets_path": (
            cfg.hf_assets_path,
            validation_hf_assets_path,
        ),
        "trainer_dp_replicate": (
            cfg.trainer.parallelism.data_parallel_replicate_degree,
            3,
        ),
        "trainer_dp_shard": (cfg.trainer.parallelism.data_parallel_shard_degree, 8),
        "generator_world_size": (generator_world_size, 8),
        "generator_dp": (cfg.generator.parallelism.data_parallel_degree, 8),
        "generator_tp": (cfg.generator.parallelism.tensor_parallel_degree, 1),
        "num_generators": (cfg.num_generators, 8),
        "eval_generator_world_size": (eval_generator_world_size, 8),
        "eval_generator_dp": (
            cfg.eval_generator_parallelism().data_parallel_degree,
            8,
        ),
        "eval_generator_tp": (
            cfg.eval_generator_parallelism().tensor_parallel_degree,
            1,
        ),
        "num_eval_generators": (cfg.num_eval_generators, num_eval_generators),
        "num_eval_rollout_workers": (
            cfg.num_eval_rollout_workers,
            num_eval_rollout_workers,
        ),
        "eval_rollout_concurrency": (
            cfg.eval_rollout_concurrency,
            eval_rollout_concurrency,
        ),
        "generator_fallback_router": (
            type(
                cfg.generator.intra_generator_router.strategy.fallback_strategy
            ).__qualname__,
            "LeastLoadedRoutingStrategy.Config",
        ),
        "generator_sticky_rebalance": (
            cfg.generator.intra_generator_router.strategy.rebalance_load_ratio,
            0.0,
        ),
        "num_training_steps": (cfg.async_loop.num_training_steps, 150),
        "groups_per_step": (cfg.async_loop.num_groups_per_train_step, 32),
        "group_size": (cfg.async_loop.group_size, 16),
        "rollout_concurrency": (cfg.rollouter.rollout_concurrency, 1500),
        "rollout_workers": (cfg.num_rollout_workers, 16),
        "generator_max_num_seqs": (cfg.async_loop.generator_max_num_seqs, 32),
        "max_active_groups": (cfg.async_loop.max_active_rollout_groups, 160),
        "initial_active_groups": (cfg.async_loop.initial_active_rollout_groups, 160),
        "max_offpolicy_steps": (cfg.async_loop.max_offpolicy_steps, 5),
        "selection_window": (
            cfg.async_loop.group_buffer.num_groups_in_selection_window,
            None,
        ),
        "drop_zero_std": (
            cfg.async_loop.training_sample_builder.drop_zero_std_reward_groups,
            False,
        ),
        "skip_zero_advantage": (
            cfg.async_loop.batcher.skip_zero_advantage_samples,
            True,
        ),
        "harder_ratio": (cfg.rollouter.evolution_harder_ratio, 0.9),
        "easier_ratio": (cfg.rollouter.evolution_easier_ratio, 0.0),
        "simplify_enabled": (
            os.environ.get("SWE_EVOLVE_SIMPLIFY"),
            expected_simplify,
        ),
        "simplify_hint": (os.environ.get("SWE_SIMPLIFY_HINT"), "vague"),
        "easier_min_scored_attempts": (
            os.environ.get("EVOLVE_EASIER_MIN_SCORED_ATTEMPTS"),
            "16",
        ),
        "context_notice_style": (
            os.environ.get("TMAX_CONTEXT_NOTICE_STYLE"),
            "della",
        ),
        "context_warn_frac": (
            os.environ.get("TMAX_CONTEXT_WARN_FRAC"),
            "0.5",
        ),
        "reward_mode": (cfg.rollouter.reward_mode, "sparse"),
        "rollout_time_budget_sec": (cfg.rollouter.time_budget_sec, 2400),
        "agent_budget_floor_sec": (cfg.rollouter.agent_budget_floor_sec, 7200),
        "verifier_timeout_sec": (cfg.rollouter.eval_timeout_sec, 600),
        "max_context_tokens": (cfg.rollouter.max_context_tokens, 63488),
        "generator_backend": (cfg.generator.backend, "torchtitan_wrapper"),
        "generator_dp_model_dtype": (cfg.generator.model_dtype, "bfloat16"),
        "generator_mamba_ssm_cache_dtype": (
            cfg.generator.mamba_ssm_cache_dtype,
            "float32",
        ),
        "generator_gpu_memory_limit": (cfg.generator.gpu_memory_limit, 0.6),
        "generator_temperature": (cfg.generator.sampling.temperature, 1.0),
        "generator_top_p": (cfg.generator.sampling.top_p, 1.0),
        "generator_max_tokens": (cfg.generator.sampling.max_tokens, 32768),
        "generator_cudagraph": (cfg.generator.cudagraph.enable, True),
        "generator_cudagraph_mode": (
            cfg.generator.cudagraph.mode,
            "FULL_DECODE_ONLY",
        ),
        "prefix_cache": (cfg.generator.enable_prefix_caching, True),
        "salt_prefix_cache": (cfg.generator.salt_prefix_cache_on_weight_sync, True),
        "reset_prefix_cache": (
            cfg.generator.reset_prefix_cache_on_weight_sync,
            False,
        ),
        "loss_chunks": (cfg.trainer.loss.num_chunks, 32),
        "loss_type": (
            type(cfg.trainer.loss.loss_fn).__qualname__,
            "DPPOLoss.Config",
        ),
        "dppo_divergence_type": (cfg.trainer.loss.loss_fn.divergence_type, "tv"),
        "dppo_divergence_threshold": (
            cfg.trainer.loss.loss_fn.divergence_threshold,
            0.1,
        ),
        "dppo_ratio_cap": (cfg.trainer.loss.loss_fn.ratio_cap, 0.0),
        "activation_checkpointing": (
            type(cfg.trainer.ac_config).__qualname__,
            "FullAC.Config",
        ),
        "train_sequence_length": (cfg.trainer.training.seq_len, 65536),
        "packed_sequence_length": (cfg.async_loop.batcher.batch.seq_len, 65536),
        "packed_local_batch_size": (
            cfg.async_loop.batcher.batch.local_batch_size,
            1,
        ),
        "checkpoint_interval": (
            cfg.trainer.checkpoint.interval,
            int(os.environ.get("SWE_CKPT_INTERVAL", "5")),
        ),
        "checkpoint_first_step": (
            cfg.trainer.checkpoint.enable_first_step_checkpoint,
            os.environ.get("SWE_CKPT_FIRST_STEP", "0") == "1",
        ),
        "checkpoint_controller_data": (cfg.trainer.checkpoint_training_data, True),
        "checkpoint_last_save_model_only": (
            cfg.trainer.checkpoint.last_save_model_only,
            False,
        ),
        "training_heartbeat_interval": (cfg.training_heartbeat_interval_sec, 30),
        "rollout_attempt_isolation": (cfg.isolate_rollout_artifacts, True),
        "checkpoint_keep": (cfg.trainer.checkpoint.keep_latest_k, 2),
        "checkpoint_async": (cfg.trainer.checkpoint.async_mode, "async"),
        "validation_samples": (cfg.async_loop.validation.num_samples, 89),
        "validation_interval": (cfg.async_loop.validation.interval, 20),
        "validation_group_size": (cfg.async_loop.validation.group_size, 5),
        "validation_async": (cfg.async_loop.validation.run_async, True),
        "validation_temperature": (cfg.async_loop.validation.temperature, 1.0),
        "validation_top_p": (cfg.async_loop.validation.top_p, 1.0),
        "validation_traces": (cfg.async_loop.validation.trace.enable, True),
        "train_shuffle": (cfg.rollouter.train_dataset.shuffle, True),
        "train_holdout": (cfg.rollouter.train_dataset.holdout_n, 0),
        "training_dtype": (cfg.trainer.training.dtype, "float32"),
        "mixed_precision_param": (
            cfg.trainer.training.mixed_precision_param,
            "bfloat16",
        ),
        "mixed_precision_reduce": (
            cfg.trainer.training.mixed_precision_reduce,
            "float32",
        ),
        "optimizer_implementation": (cfg.trainer.optimizer.implementation, "fused"),
        "optimizer_name": (optimizer_group.optimizer_name, "AdamW"),
        "optimizer_kwargs": (
            optimizer_group.optimizer_kwargs,
            {
                "lr": 1e-6,
                "betas": (0.9, 0.999),
                "eps": 1e-8,
                "weight_decay": 0.0,
            },
        ),
        "lr_warmup_steps": (cfg.trainer.lr_scheduler.warmup_steps, 1),
        "lr_min_factor": (cfg.trainer.lr_scheduler.min_lr_factor, 1.0),
        "thinking_retention": (cfg.renderer.thinking_retention, "all"),
        "tmax_agent": (os.environ.get("TMAX_AGENT"), "terminus"),
        "terminus_max_turns": (os.environ.get("TMAX_TERMINUS_MAX_TURNS"), "120"),
        "exec_timeout_sec": (os.environ.get("TMAX_EXEC_TIMEOUT_SEC"), "120"),
        "rollout_records": (os.environ.get("SWE_ROLLOUT_RECORDS"), "1"),
        "evolution_signals": (os.environ.get("SWE_EVOLUTION_SIGNALS"), "1"),
        "data_hot_reload": (os.environ.get("SWE_DATA_HOT_RELOAD"), "1"),
        "train_data": (
            cfg.rollouter.train_dataset.data_path,
            os.environ["SWE_PROMPT_DATA"],
        ),
        "validation_data": (
            cfg.rollouter.validation_dataset.data_path,
            os.environ["SWE_TB2_VAL_DATA"],
        ),
    }
    for label, (actual, expected) in checks.items():
        _expect(label, actual, expected)

    result = {key: actual for key, (actual, _expected) in checks.items()}
    result["controller_class"] = cfg._owner.__qualname__
    result["spec_repair_enabled"] = os.environ.get("EVOLVE_SPEC_REPAIR", "0") == "1"
    result["student_difficulty_feedback"] = (
        "paired_policy_canary" if policy_canary_k else "training_rollouts"
    )
    result["extra_student_rollouts_per_candidate"] = 2 * policy_canary_k
    if policy_canary_k:
        result["policy_canary"] = {
            "group_size": cfg.policy_canary.group_size,
            "max_concurrency": cfg.policy_canary.max_concurrency,
            "max_version_span": cfg.policy_canary.max_version_span,
            "timeout_sec": cfg.policy_canary.timeout_sec,
            "poll_sec": cfg.policy_canary.poll_sec,
            "root": cfg.policy_canary_root,
        }
    result.update(
        {
            "rollouts_per_step": (
                cfg.async_loop.num_groups_per_train_step * cfg.async_loop.group_size
            ),
            "trainer_nodes": trainer_world_size // 8,
            "generator_nodes": cfg.num_generators * (generator_world_size // 8),
            "eval_nodes": cfg.num_eval_generators * (eval_generator_world_size // 8),
            "controller_nodes": 1,
            "total_nodes": 1
            + trainer_world_size // 8
            + cfg.num_generators * (generator_world_size // 8)
            + cfg.num_eval_generators * (eval_generator_world_size // 8),
            "optimizer_steps": cfg.async_loop.num_training_steps,
            "optimizer": {
                "implementation": cfg.trainer.optimizer.implementation,
                "param_groups": [
                    {
                        "pattern": group.pattern,
                        "optimizer_name": group.optimizer_name,
                        "optimizer_kwargs": group.optimizer_kwargs,
                    }
                    for group in cfg.trainer.optimizer.param_groups
                ],
            },
            "training_dtype": cfg.trainer.training.dtype,
            "mixed_precision_param": cfg.trainer.training.mixed_precision_param,
            "mixed_precision_reduce": cfg.trainer.training.mixed_precision_reduce,
        }
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
