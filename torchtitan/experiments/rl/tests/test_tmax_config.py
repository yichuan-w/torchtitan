# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import asyncio
import dataclasses
import os

import pytest


def test_model_only_continuation_and_train_stream_offset(monkeypatch):
    monkeypatch.setenv("SWE_INITIAL_DCP_CHECKPOINT", "/checkpoints/step-200")
    monkeypatch.setenv("SWE_INITIAL_POLICY_VERSION", "200")
    monkeypatch.setenv("SWE_INITIAL_SKIP_SAMPLES", "7926")
    monkeypatch.setenv("SWE_TRAIN_STEPS", "400")
    monkeypatch.setenv("SWE_LAST_SAVE_MODEL_ONLY", "0")
    config = rl_grpo_qwen3_5_9b_tmax()
    assert config.trainer.initial_policy_version == 200
    assert config.trainer.checkpoint.initial_load_path == "/checkpoints/step-200"
    assert config.trainer.checkpoint.initial_load_model_only
    assert not config.trainer.checkpoint.initial_load_in_hf
    assert not config.trainer.checkpoint.last_save_model_only
    assert config.async_loop.num_training_steps == 400
    assert config.rollouter.train_dataset.initial_skip_samples == 7926
    assert config.rollouter.validation_dataset.initial_skip_samples == 0

@pytest.mark.parametrize("override,expected", [(None, False), ("1", True)])
def test_final_save_keeps_optimizer_by_default(monkeypatch, override, expected):
    if override is None:
        monkeypatch.delenv("SWE_LAST_SAVE_MODEL_ONLY", raising=False)
    else:
        monkeypatch.setenv("SWE_LAST_SAVE_MODEL_ONLY", override)
    config = rl_grpo_qwen3_5_9b_tmax()
    assert config.trainer.checkpoint.last_save_model_only is expected


from torchtitan.experiments.rl.examples.tmax import (
    config_registry as tmax_config_registry,
)
from torchtitan.experiments.rl.examples.tmax.config_registry import (
    rl_grpo_qwen3_4b_tmax,
    rl_grpo_qwen3_5_9b_tmax,
)
from torchtitan.experiments.rl.rollout.types import Rollout, RolloutStatus


@pytest.fixture(autouse=True)
def separate_validation_dataset(monkeypatch):
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", "/data/tb2.jsonl")


@pytest.mark.parametrize("turn_cap", [None, 8192, 32768])
@pytest.mark.parametrize("eval_cap", [None, 24576])
def test_tmax_9b_turn_token_override(
    monkeypatch: pytest.MonkeyPatch, turn_cap: int | None, eval_cap: int | None
) -> None:
    if turn_cap is None:
        monkeypatch.delenv("TMAX_TURN_MAX_TOKENS", raising=False)
    else:
        monkeypatch.setenv("TMAX_TURN_MAX_TOKENS", str(turn_cap))
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", "/data/tb2.jsonl")
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_MAX_TOKENS", eval_cap)

    config = rl_grpo_qwen3_5_9b_tmax()

    assert config.generator.sampling.max_tokens == (turn_cap or 16384)
    assert config.async_loop.validation.max_tokens == eval_cap
    assert config.async_loop.batcher.batch.seq_len == 65536


@pytest.mark.parametrize("holdout", [None, 0, 16])
def test_tmax_holdout_override_with_separate_eval(monkeypatch, holdout):
    if holdout is None:
        monkeypatch.delenv("SWE_HOLDOUT_N", raising=False)
    else:
        monkeypatch.setenv("SWE_HOLDOUT_N", str(holdout))
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", "/data/tb2.jsonl")

    config = tmax_config_registry._tmax_rollouter()

    assert config.train_dataset.holdout_n == (0 if holdout is None else holdout)
    assert config.validation_dataset.data_path == "/data/tb2.jsonl"
    assert config.validation_dataset.holdout_n == 0


def test_tmax_holdout_override_keeps_train_and_validation_disjoint(monkeypatch):
    monkeypatch.setenv("SWE_HOLDOUT_N", "48")
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", "")
    config = tmax_config_registry._tmax_rollouter()
    assert config.train_dataset.holdout_n == 48
    assert config.train_dataset.split == "train"
    assert config.validation_dataset.holdout_n == 48
    assert config.validation_dataset.split == "validation"


@pytest.mark.parametrize("holdout", [None, "0"])
@pytest.mark.parametrize("validation_samples", ["0", "32"])
def test_tmax_zero_holdout_requires_separate_or_disabled_eval(
    monkeypatch, validation_samples, holdout
):
    if holdout is None:
        monkeypatch.delenv("SWE_HOLDOUT_N", raising=False)
    else:
        monkeypatch.setenv("SWE_HOLDOUT_N", holdout)
    monkeypatch.setenv("SWE_VAL_SAMPLES", validation_samples)
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", "")
    if validation_samples == "0":
        assert tmax_config_registry._tmax_rollouter().train_dataset.holdout_n == 0
    else:
        with pytest.raises(ValueError, match="requires a separate SWE_TB2_VAL_DATA"):
            tmax_config_registry._tmax_rollouter()


def test_tmax_negative_holdout_is_rejected(monkeypatch):
    monkeypatch.setenv("SWE_HOLDOUT_N", "-1")
    with pytest.raises(ValueError, match="must be nonnegative"):
        tmax_config_registry._tmax_rollouter()


def test_five_independent_generators_keep_controller_affinity(monkeypatch):
    from torchtitan.experiments.rl.routing.strategies import (
        LeastLoadedRoutingStrategy,
        StickySessionRoutingStrategy,
    )
    from torchtitan.experiments.rl.train import _compute_generator_world_size

    monkeypatch.setenv("SWE_NUM_GENERATORS", "5")
    monkeypatch.setenv("SWE_GEN_DP", "1")
    monkeypatch.setenv("SWE_DP_FALLBACK_ROUTER", "leastloaded")
    monkeypatch.setenv("SWE_DP_STICKY_REBALANCE", "0")
    monkeypatch.setenv("SWE_NUM_EVAL_GENERATORS", "1")
    monkeypatch.setenv("SWE_EVAL_GEN_DP", "1")
    config = rl_grpo_qwen3_5_9b_tmax()
    assert config.num_generators == 5
    assert _compute_generator_world_size(config.generator.parallelism) == 1
    strategy = config.generator_router.strategy
    assert isinstance(strategy, StickySessionRoutingStrategy.Config)
    assert isinstance(strategy.fallback_strategy, LeastLoadedRoutingStrategy.Config)
    assert strategy.rebalance_load_ratio == 0
    assert config.num_eval_generators == 1
    assert config.eval_generator_data_parallel_degree == 1


def test_invalid_generator_replica_count(monkeypatch):
    monkeypatch.setenv("SWE_NUM_GENERATORS", "0")
    with pytest.raises(ValueError, match="SWE_NUM_GENERATORS"):
        rl_grpo_qwen3_5_9b_tmax()


@pytest.mark.parametrize("canary_k", [None, "0"])
def test_policy_canary_recipe_is_opt_in(monkeypatch, canary_k):
    from torchtitan.experiments.rl.controller import Controller

    if canary_k is None:
        monkeypatch.delenv("SWE_POLICY_CANARY_K", raising=False)
    else:
        monkeypatch.setenv("SWE_POLICY_CANARY_K", canary_k)
    config = rl_grpo_qwen3_5_9b_tmax()
    assert config._owner is Controller
    assert not hasattr(config, "policy_canary")


def test_policy_canary_recipe_preserves_training_config(monkeypatch):
    from torchtitan.experiments.rl.examples.tmax.policy_canary_controller import (
        PolicyCanaryController,
    )

    monkeypatch.setenv("TMAX_AGENT", "terminus")
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    baseline = rl_grpo_qwen3_5_9b_tmax()
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "8")
    monkeypatch.setenv("SWE_POLICY_CANARY_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("SWE_POLICY_CANARY_MAX_VERSION_SPAN", "5")
    monkeypatch.setenv("SWE_POLICY_CANARY_TIMEOUT_SEC", "7200")
    monkeypatch.setenv("SWE_POLICY_CANARY_POLL_SEC", "10")
    monkeypatch.setenv("TRL_BASE", "/data/experiment")

    config = rl_grpo_qwen3_5_9b_tmax()

    assert isinstance(config, PolicyCanaryController.Config)
    assert config._owner is PolicyCanaryController
    assert config.policy_canary.group_size == 8
    assert config.policy_canary.max_concurrency == 4
    assert config.policy_canary.max_version_span == 5
    assert config.policy_canary.timeout_sec == 7200
    assert config.policy_canary.poll_sec == 10
    assert config.policy_canary_root == "/data/experiment"
    assert config.trainer == baseline.trainer
    assert config.generator == baseline.generator
    assert config.async_loop == baseline.async_loop
    assert config.rollouter == baseline.rollouter


@pytest.mark.parametrize("root", ("", "relative/root"))
def test_policy_canary_requires_an_absolute_root(monkeypatch, root):
    monkeypatch.setenv("TMAX_AGENT", "terminus")
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "8")
    monkeypatch.setenv("TRL_BASE", root)
    with pytest.raises(ValueError, match="absolute experiment root"):
        rl_grpo_qwen3_5_9b_tmax()


def test_policy_canary_requires_terminus(monkeypatch):
    monkeypatch.setenv("TMAX_AGENT", "vanillux")
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "8")
    monkeypatch.setenv("TRL_BASE", "/data/experiment")
    with pytest.raises(ValueError, match="training Terminus scaffold"):
        rl_grpo_qwen3_5_9b_tmax()


@pytest.mark.parametrize("override,expected", [(None, 1.0), ("0.9", 0.9)])
def test_evolution_harder_ratio_env(monkeypatch, override, expected):
    if override is None:
        monkeypatch.delenv("SWE_EVOLUTION_HARDER_RATIO", raising=False)
    else:
        monkeypatch.setenv("SWE_EVOLUTION_HARDER_RATIO", override)
    assert tmax_config_registry._tmax_rollouter().evolution_harder_ratio == expected


@pytest.mark.parametrize(
    ("lr_override", "expected_lr"),
    [(None, 1e-6), ("2e-7", 2e-7)],
)
def test_tmax_9b_uses_open_instruct_optimizer_and_mixed_precision(
    monkeypatch: pytest.MonkeyPatch,
    lr_override: str | None,
    expected_lr: float,
) -> None:
    monkeypatch.delenv("SWE_GDN_BI", raising=False)
    if lr_override is None:
        monkeypatch.delenv("SWE_LR", raising=False)
    else:
        monkeypatch.setenv("SWE_LR", lr_override)

    config = rl_grpo_qwen3_5_9b_tmax()

    training = config.trainer.training
    assert training.dtype == "float32"
    assert training.mixed_precision_param == "bfloat16"
    assert training.mixed_precision_reduce == "float32"

    optimizer = config.trainer.optimizer
    assert optimizer.implementation == "fused"
    assert len(optimizer.param_groups) == 1
    param_group = optimizer.param_groups[0]
    assert param_group.optimizer_name == "AdamW"
    assert param_group.optimizer_kwargs == {
        "lr": expected_lr,
        "betas": (0.9, 0.999),
        "eps": 1e-8,
        "weight_decay": 0.0,
    }


def test_tmax_batch_invariant_uses_fp32_master_and_bf16_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SWE_LR", raising=False)
    monkeypatch.setenv("SWE_GDN_BI", "1")

    config = rl_grpo_qwen3_5_9b_tmax()

    assert config.trainer.debug.batch_invariant
    training = config.trainer.training
    assert training.dtype == "float32"
    assert training.mixed_precision_param == "bfloat16"
    assert training.mixed_precision_reduce == "float32"


def test_tmax_4b_batch_invariant_uses_fp32_master_and_bf16_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SWE_GDN_BI", raising=False)
    monkeypatch.delenv("SWE_LR", raising=False)

    config = rl_grpo_qwen3_4b_tmax()

    assert config.trainer.debug.batch_invariant
    training = config.trainer.training
    assert training.dtype == "float32"
    assert training.mixed_precision_param == "bfloat16"
    assert training.mixed_precision_reduce == "float32"


@pytest.mark.parametrize(
    ("time_budget_override", "expected_time_budget"),
    [(None, 1900), ("1800", 1800)],
)
def test_tmax_time_budget(
    monkeypatch: pytest.MonkeyPatch,
    time_budget_override: str | None,
    expected_time_budget: int,
) -> None:
    if time_budget_override is None:
        monkeypatch.delenv("SWE_TIME_BUDGET_SEC", raising=False)
    else:
        monkeypatch.setenv("SWE_TIME_BUDGET_SEC", time_budget_override)

    config = rl_grpo_qwen3_5_9b_tmax()

    assert config.rollouter.time_budget_sec == expected_time_budget


def test_tmax_include_ids_only_filters_training_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    include_path = "/mnt/shared/tmax-medium-difficulty-ids.txt"
    monkeypatch.setattr(tmax_config_registry, "_INCLUDE_IDS", include_path)

    config = rl_grpo_qwen3_5_9b_tmax()

    assert config.rollouter.train_dataset.include_ids_path == include_path
    assert config.rollouter.validation_dataset.include_ids_path == ""


def test_tmax_9b_configures_bs32_spp8_no_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWE_NUM_GROUPS_PER_TRAIN_STEP", "32")
    monkeypatch.setenv("SWE_GROUP_SIZE", "8")
    monkeypatch.setenv("SWE_DROP_ZERO_STD", "0")
    monkeypatch.setenv("SWE_ROLLOUT_CONCURRENCY", "512")
    monkeypatch.setenv("SWE_NUM_ROLLOUT_WORKERS", "8")
    monkeypatch.setenv("SWE_MAX_ACTIVE_GROUPS", "112")
    monkeypatch.setenv("SWE_INITIAL_ACTIVE_GROUPS", "80")

    async_loop = rl_grpo_qwen3_5_9b_tmax().async_loop

    assert async_loop.num_groups_per_train_step == 32
    assert async_loop.group_size == 8
    assert not async_loop.training_sample_builder.drop_zero_std_reward_groups
    assert async_loop.max_active_rollout_groups == 112
    assert async_loop.initial_active_rollout_groups == 80


def test_tmax_keeps_zero_reward_infra_siblings_for_oi_parity() -> None:
    config = rl_grpo_qwen3_5_9b_tmax()
    builder = config.async_loop.training_sample_builder

    assert not builder.drop_groups_with_untrainable_rollouts

    rubric_config = config.rollouter.rubric
    assert rubric_config.error_reward is None
    assert rubric_config.truncation_reward is None

    rollout = Rollout(
        group_id=0,
        rollout_id=0,
        status=RolloutStatus.ERROR,
    )
    output = asyncio.run(rubric_config.build().score_group([rollout], object()))[0]

    assert output.reward == 0.0
    assert output.reward_breakdown == {"RewardTMax": 0.0}


@pytest.mark.parametrize(
    (
        "max_active_override",
        "initial_active_override",
        "rollout_concurrency",
        "expected_max_active",
        "expected_initial_active",
    ),
    [
        (None, None, 512, 40, 32),
        (None, None, 1000, 40, 32),
        (None, None, 1024, 40, 40),
        ("32", None, 512, 32, 32),
        ("40", "24", 1024, 40, 24),
    ],
)
def test_tmax_9b_cold_start_capacity(
    monkeypatch: pytest.MonkeyPatch,
    max_active_override: str | None,
    initial_active_override: str | None,
    rollout_concurrency: int,
    expected_max_active: int,
    expected_initial_active: int,
) -> None:
    monkeypatch.delenv("SWE_GDN_BI", raising=False)
    monkeypatch.delenv("SWE_OFFPOLICY_STEPS", raising=False)
    monkeypatch.delenv("SWE_SELECTION_WINDOW_GROUPS", raising=False)
    monkeypatch.delenv("SWE_MAX_BYPASS_GROUPS", raising=False)
    monkeypatch.delenv("SWE_STRICT_FIFO", raising=False)
    monkeypatch.setenv("SWE_NUM_ROLLOUT_WORKERS", "8")
    monkeypatch.setenv("SWE_ROLLOUT_CONCURRENCY", str(rollout_concurrency))
    for name, value in (
        ("SWE_MAX_ACTIVE_GROUPS", max_active_override),
        ("SWE_INITIAL_ACTIVE_GROUPS", initial_active_override),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    async_loop = rl_grpo_qwen3_5_9b_tmax().async_loop

    assert async_loop.max_offpolicy_steps == 4
    assert async_loop.max_active_rollout_groups == expected_max_active
    assert async_loop.initial_active_rollout_groups == expected_initial_active
    assert async_loop.resolved_max_active_rollout_groups() == expected_max_active
    assert (
        async_loop.resolved_initial_active_rollout_groups() == expected_initial_active
    )


def test_tmax_9b_defaults_to_unbounded_take_any(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SWE_SELECTION_WINDOW_GROUPS", raising=False)
    monkeypatch.delenv("SWE_MAX_BYPASS_GROUPS", raising=False)
    monkeypatch.delenv("SWE_STRICT_FIFO", raising=False)

    group_buffer = rl_grpo_qwen3_5_9b_tmax().async_loop.group_buffer

    assert group_buffer.num_groups_in_selection_window is None
    assert group_buffer.max_bypass_groups is None
    assert not group_buffer.strict_fifo


def test_tmax_9b_rejects_worker_split_without_group_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SWE_INITIAL_ACTIVE_GROUPS", raising=False)
    monkeypatch.setenv("SWE_MAX_ACTIVE_GROUPS", "40")
    monkeypatch.setenv("SWE_NUM_ROLLOUT_WORKERS", "15")
    monkeypatch.setenv("SWE_ROLLOUT_CONCURRENCY", "1024")

    with pytest.raises(ValueError, match="cannot keep every trajectory gate supplied"):
        rl_grpo_qwen3_5_9b_tmax()


def test_tmax_9b_configures_sliding_selection_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWE_SELECTION_WINDOW_GROUPS", "12")
    monkeypatch.delenv("SWE_MAX_BYPASS_GROUPS", raising=False)
    monkeypatch.setenv("SWE_STRICT_FIFO", "0")

    group_buffer = rl_grpo_qwen3_5_9b_tmax().async_loop.group_buffer

    assert group_buffer.num_groups_in_selection_window == 12
    assert group_buffer.max_bypass_groups is None
    assert not group_buffer.strict_fifo


@pytest.mark.parametrize(
    ("max_bypass_override", "expected"),
    [("32", 32), ("off", None), ("", None)],
)
def test_tmax_9b_configures_max_bypass_override(
    monkeypatch: pytest.MonkeyPatch,
    max_bypass_override: str,
    expected: int | None,
) -> None:
    monkeypatch.setenv("SWE_SELECTION_WINDOW_GROUPS", "12")
    monkeypatch.setenv("SWE_MAX_BYPASS_GROUPS", max_bypass_override)

    group_buffer = rl_grpo_qwen3_5_9b_tmax().async_loop.group_buffer

    assert group_buffer.max_bypass_groups == expected


_CONTEXT_ENV = (
    "SWE_TMAX_CONTEXT",
    "SWE_MAX_CONTEXT_LEN",
    "SWE_EVAL_MAX_MODEL_LEN",
    "SWE_EVAL_MAX_CONTEXT_LEN",
)


def _eval_generators_on(monkeypatch):
    monkeypatch.setenv("SWE_NUM_EVAL_GENERATORS", "2")
    monkeypatch.setenv("SWE_EVAL_GEN_DP", "8")
    monkeypatch.setenv("SWE_NUM_EVAL_ROLLOUT_WORKERS", "16")


def _engine_max_model_len(model_spec) -> int:
    # What VLLMGenerator passes as engine_kwargs["max_model_len"] (both backends)
    # and what the torchtitan_wrapper HF config advertises as the position cap.
    from torchtitan.experiments.rl.models.vllm_registry import (
        model_spec_to_hf_config_dict,
    )

    hf = model_spec_to_hf_config_dict(model_spec)
    assert hf["max_position_embeddings"] == model_spec.model.max_seq_len
    return model_spec.model.max_seq_len


def test_tmax_context_defaults_unchanged(monkeypatch):
    for name in _CONTEXT_ENV:
        monkeypatch.delenv(name, raising=False)
    _eval_generators_on(monkeypatch)
    config = rl_grpo_qwen3_5_9b_tmax()
    # tyro rebuilds the Controller.Config at launch, which runs __post_init__ and
    # mirrors the batcher width into the trainer seq_len.
    rebuilt = dataclasses.replace(config)

    assert config.async_loop.batcher.batch.seq_len == 65536
    assert rebuilt.trainer.training.seq_len == 65536
    assert _engine_max_model_len(config.model_spec) == 65536
    assert config.eval_generator_max_model_len == 0
    assert config.eval_generator_model_spec() is config.model_spec
    assert config.rollouter.max_context_tokens == 65536 - 2048
    assert config.rollouter.eval_max_context_tokens == 0


def test_tmax_context_train_80k_eval_160k(monkeypatch):
    _eval_generators_on(monkeypatch)
    monkeypatch.setenv("SWE_TMAX_CONTEXT", "81920")
    monkeypatch.setenv("SWE_MAX_CONTEXT_LEN", "79872")
    monkeypatch.setenv("SWE_EVAL_MAX_MODEL_LEN", "163840")
    monkeypatch.setenv("SWE_EVAL_MAX_CONTEXT_LEN", "161792")
    config = rl_grpo_qwen3_5_9b_tmax()
    rebuilt = dataclasses.replace(config)

    # Training generators, trainer and batcher: 81920.
    assert config.async_loop.batcher.batch.seq_len == 81920
    assert rebuilt.trainer.training.seq_len == 81920
    assert _engine_max_model_len(config.model_spec) == 81920
    assert 81920 % config.trainer.loss.num_chunks == 0
    # Eval generators: their own copy at 163840; the training spec is untouched.
    eval_spec = config.eval_generator_model_spec()
    assert eval_spec is not config.model_spec
    assert _engine_max_model_len(eval_spec) == 163840
    assert _engine_max_model_len(config.model_spec) == 81920
    assert all(
        layer.attention.rope.max_seq_len == 163840
        for layer in eval_spec.model.layers
        if getattr(layer, "attention", None) is not None
    )
    # Agent-side caps.
    assert config.rollouter.max_context_tokens == 79872
    assert config.rollouter.eval_max_context_tokens == 161792


def test_tmax_eval_context_must_fit_eval_model_len(monkeypatch):
    for name in _CONTEXT_ENV:
        monkeypatch.delenv(name, raising=False)
    # No eval generators: validation runs on the 65536 training generators.
    monkeypatch.setenv("SWE_NUM_EVAL_GENERATORS", "0")
    monkeypatch.setenv("SWE_EVAL_MAX_CONTEXT_LEN", "161792")
    with pytest.raises(ValueError, match="SWE_EVAL_MAX_CONTEXT_LEN=161792"):
        rl_grpo_qwen3_5_9b_tmax()


_TB2_1_EVAL = (
    "/home/yichuan/torchtitan-claude-code-harness-cotrain/mast_rl/swe_assets/"
    "tb2_1_envpatch_20261016/tb2_1_eval.jsonl"
)


@pytest.mark.skipif(not os.path.isfile(_TB2_1_EVAL), reason="TB-2.1 eval set absent")
@pytest.mark.parametrize(("eval_cap", "expected"), [(0, 79872), (161792, 161792)])
def test_tmax_rollouter_eval_cap_resolution(eval_cap, expected):
    from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset
    from torchtitan.experiments.rl.examples.tmax.rollouter import TMaxRollouter

    dataset = TMaxDataset.Config(data_path=_TB2_1_EVAL, holdout_n=0, shuffle=False)
    rollouter = TMaxRollouter.Config(
        train_dataset=dataset,
        validation_dataset=dataset,
        max_context_tokens=79872,
        eval_max_context_tokens=eval_cap,
    ).build()
    # Training rollouts keep the training cap; validation (negative group ids)
    # uses the eval cap, which falls back to the training cap when unset.
    assert rollouter._max_context_tokens == 79872
    assert rollouter._eval_max_context_tokens == expected



def test_eval_max_turns_env(monkeypatch):
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", "/data/tb2.jsonl")
    monkeypatch.delenv("SWE_EVAL_MAX_TURNS", raising=False)
    assert tmax_config_registry.rl_grpo_qwen3_5_9b_tmax().rollouter.eval_max_turns == 0
    monkeypatch.setenv("SWE_EVAL_MAX_TURNS", "-1")
    assert tmax_config_registry.rl_grpo_qwen3_5_9b_tmax().rollouter.eval_max_turns == -1
