# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Named validation sets scored in the same pass as the main one.

The main set (``SWE_TB2_VAL_DATA``) must keep its metric names, its calls and its
trace layout when it is the only set; each named set (``SWE_VAL_SETS``) gets its
own sample count (its file's row count by default), sampling, ``validation/<name>/``
namespace and trace directory.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl import controller as controller_mod
from torchtitan.experiments.rl.actors.generator import SamplingConfig
from torchtitan.experiments.rl.controller import (
    _check_validation_sets,
    _will_validate_after_step,
    Controller,
    ValidationConfig,
    ValidationSetConfig,
)
from torchtitan.experiments.rl.eval_trace_recorder import ValidationTraceRecorder
from torchtitan.experiments.rl.examples.tmax import (
    config_registry as tmax_config_registry,
)
from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset
from torchtitan.experiments.rl.observability import metrics as m
from torchtitan.experiments.rl.rollout import Rollout, RolloutGroup, RolloutStatus
from torchtitan.experiments.rl.rollout.rollouter import Rollouter


def _write_rows(path, ids):
    path.write_text(
        "".join(
            json.dumps(
                {
                    "prompt": f"solve {task}",
                    "metadata": {"instance_id": task, "image": "img", "tmax": {"t": 1}},
                }
            )
            + "\n"
            for task in ids
        )
    )
    return str(path)


# --------------------------------------------------------------------------- #
# Config objects
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["tb4", "tb21", "x"])
def test_set_names_that_are_accepted(name):
    assert ValidationSetConfig(name=name).name == name


@pytest.mark.parametrize(
    "name",
    ["", "TB4", "tb-4", "tb_4", "4tb", "tb 4", "valid", "skipped", "deferred", "reward"],
)
def test_set_names_that_are_refused(name):
    with pytest.raises(ValueError, match="validation set name"):
        ValidationSetConfig(name=name)


@pytest.mark.parametrize(
    "field,value", [("num_samples", -2), ("group_size", -1), ("every", 0)]
)
def test_set_fields_out_of_range(field, value):
    with pytest.raises(ValueError, match=field):
        ValidationSetConfig(name="tb4", **{field: value})


def test_set_names_must_be_unique():
    with pytest.raises(ValueError, match="unique"):
        ValidationConfig(sets=[ValidationSetConfig(name="tb4")] * 2)


def test_a_named_set_alone_enables_validation():
    only_set = ValidationConfig(
        num_samples=0, interval=10, sets=[ValidationSetConfig(name="tb4")]
    )
    assert only_set.enabled
    assert _will_validate_after_step(
        validation=only_set, step=20, num_training_steps=100
    )
    empty_set = ValidationConfig(
        num_samples=0, sets=[ValidationSetConfig(name="tb4", num_samples=0)]
    )
    assert not empty_set.enabled
    assert not ValidationConfig(num_samples=0).enabled
    assert ValidationConfig().enabled  # the default main set, unchanged


def test_every_named_set_needs_a_dataset_and_an_unused_one_is_warned(monkeypatch):
    validation = ValidationConfig(sets=[ValidationSetConfig(name="tb4")])
    with pytest.raises(ValueError, match=r"\['tb4'\] have no rollouter"):
        _check_validation_sets(validation, datasets={})
    warnings = []
    monkeypatch.setattr(
        controller_mod.logger, "warning", lambda *args: warnings.append(args)
    )
    _check_validation_sets(validation, datasets={"tb4": object(), "extra": object()})
    assert warnings and warnings[0][1] == ["extra"]


# --------------------------------------------------------------------------- #
# Rollouter: named datasets, row counts
# --------------------------------------------------------------------------- #
def test_rollouter_draws_from_the_named_dataset(tmp_path):
    rollouter = object.__new__(Rollouter)
    rollouter._validation_dataset = iter(["main-0", "main-1"])
    tb4 = TMaxDataset.Config(
        data_path=_write_rows(tmp_path / "tb4.jsonl", ["a", "b", "c"]),
        seed=99,
        shuffle=False,
        holdout_n=0,
    ).build()
    rollouter._validation_sets = {"tb4": tb4}
    assert rollouter.get_validation_sample() == "main-0"
    assert rollouter.get_validation_sample("tb4").instance_id == "a"
    assert rollouter.validation_set_size("tb4") == 3
    assert rollouter.validation_set_size() is None  # an iterator has no length
    with pytest.raises(KeyError, match="no validation set 'tb2'"):
        rollouter.get_validation_sample("tb2")


def test_a_rollouter_built_before_named_sets_still_serves_the_main_one():
    rollouter = object.__new__(Rollouter)
    rollouter._validation_dataset = iter(["main-0"])
    assert rollouter.get_validation_sample() == "main-0"
    with pytest.raises(KeyError, match="configured: none"):
        rollouter.validation_set_size("tb4")


def test_a_row_count_pass_scores_each_task_once_every_pass(tmp_path):
    """The cursor carries across passes: row count -> the same tasks every pass;
    any other count shifts and repeats them (the old SWE_VAL_SAMPLES gotcha)."""
    path = _write_rows(tmp_path / "tb.jsonl", ["a", "b", "c"])

    def passes(num_samples):
        dataset = TMaxDataset.Config(
            data_path=path, seed=99, shuffle=False, holdout_n=0
        ).build()
        return [
            [next(dataset).instance_id for _ in range(num_samples)] for _ in range(2)
        ]

    assert passes(3) == [["a", "b", "c"]] * 2
    assert passes(2) == [["a", "b"], ["c", "a"]]


# --------------------------------------------------------------------------- #
# Collection: per-set metric names
# --------------------------------------------------------------------------- #
def _group(group_id, rewards, metrics=()):
    return RolloutGroup(
        group_id=group_id,
        rollouts=[
            Rollout(
                group_id=group_id,
                rollout_id=index,
                status=RolloutStatus.COMPLETED,
                reward=reward,
                reward_breakdown={"tmax": reward},
            )
            for index, reward in enumerate(rewards)
        ],
        metrics=list(metrics),
    )


def _collector(samples_by_set):
    calls = []

    def get_validation_sample(*args):
        calls.append(args)
        return next(samples_by_set[args[0] if args else None])

    async def run_group(*, group_id, group_size, **kwargs):
        rewards = [1.0] + [0.0] * (group_size - 1) if group_id == -1 else [0.0] * group_size
        return _group(group_id, rewards, [m.Metric("rollout/nonsubmit_frac", m.Mean(0.5))])

    collector = SimpleNamespace(
        _rollouter=SimpleNamespace(get_validation_sample=get_validation_sample),
        _allocate_validation_group_ids=lambda count: [-(i + 1) for i in range(count)],
        _eval_rollout_workers=[],
        _run_validation_group=run_group,
    )
    return collector, calls


def _collect(collector, **kwargs):
    samples, groups, metrics = asyncio.run(
        Controller._collect_validation_rollouts(
            collector, num_groups=2, group_size=2, sampling=None, step=40, **kwargs
        )
    )
    return samples, m.MetricsProcessor._aggregate_metrics(metrics)


_MAIN_KEYS = {
    "validation_reward/_mean",
    "validation/valid",
    "validation/pass_at_k/mean",
    "validation/group_failures/sum",
    "validation/nonsubmit_frac/mean",
    "validation_reward/component/tmax/mean",
}


def test_the_main_set_keeps_its_names_and_calls():
    collector, calls = _collector({None: iter(["a", "b"])})
    _samples, aggregated = _collect(collector)
    assert calls == [(), ()]  # get_validation_sample() exactly as before
    assert _MAIN_KEYS <= set(aggregated)
    assert not any(key.startswith("validation/tb4") for key in aggregated)
    assert aggregated["validation_reward/_mean"] == 0.25
    assert aggregated["validation/pass_at_k/mean"] == 0.5


def test_a_named_set_logs_everything_under_its_own_namespace():
    collector, calls = _collector({"tb4": iter(["x", "y"])})
    samples, aggregated = _collect(collector, set_name="tb4")
    assert samples == ["x", "y"]
    assert calls == [("tb4",), ("tb4",)]
    assert {
        "validation/tb4/reward/_mean",
        "validation/tb4/valid",
        "validation/tb4/pass_at_k/mean",
        "validation/tb4/group_failures/sum",
        "validation/tb4/nonsubmit_frac/mean",
        "validation/tb4/reward/component/tmax/mean",
    } <= set(aggregated)
    assert all(key.startswith("validation/tb4/") for key in aggregated), sorted(
        aggregated
    )
    assert aggregated["validation/tb4/reward/_mean"] == 0.25
    assert aggregated["validation/tb4/pass_at_k/mean"] == 0.5
    assert aggregated["validation/tb4/valid"] == 1.0


# --------------------------------------------------------------------------- #
# validate(): one pass, every set, cadence
# --------------------------------------------------------------------------- #
def _controller(validation, *, num_training_steps=100, set_sizes=None):
    controller = object.__new__(Controller)
    controller.config = SimpleNamespace(
        async_loop=SimpleNamespace(
            validation=validation, num_training_steps=num_training_steps
        ),
        num_eval_generators=0,
    )
    controller._sampling = SamplingConfig(temperature=0.8, top_p=0.9, max_tokens=100)
    controller._rollouter = SimpleNamespace(
        validation_set_size=lambda name: (set_sizes or {})[name]
    )
    controller.rollout_recorder = SimpleNamespace(record=lambda **kwargs: None)
    controller.validation_trace_recorder = SimpleNamespace(enabled=False)
    calls = []

    async def collect(**kwargs):
        calls.append(kwargs)
        name = kwargs.get("set_name")
        prefix = "validation" if name is None else f"validation/{name}"
        return [], [], [m.Metric(f"{prefix}/valid", m.NoReduce(1.0))]

    controller._collect_validation_rollouts = collect
    return controller, calls


def test_without_named_sets_validate_makes_the_same_single_call():
    validation = ValidationConfig(num_samples=89, group_size=5, temperature=1.0, top_p=1.0)
    controller, calls = _controller(validation)
    metrics = asyncio.run(controller.validate(step=0))
    assert len(calls) == 1
    assert set(calls[0]) == {"num_groups", "group_size", "sampling", "step"}
    assert (calls[0]["num_groups"], calls[0]["group_size"]) == (89, 5)
    assert [metric.key for metric in metrics] == [
        "validation/valid",
        "validation/policy_version",
        "timing/validate",
    ]


def test_named_sets_share_the_pass_and_inherit_unset_sampling():
    validation = ValidationConfig(
        num_samples=89,
        group_size=5,
        temperature=1.0,
        top_p=1.0,
        max_tokens=None,
        sets=[
            ValidationSetConfig(name="tb4"),
            ValidationSetConfig(
                name="probe", num_samples=3, group_size=2, temperature=0.0, max_tokens=7
            ),
        ],
    )
    controller, calls = _controller(validation, set_sizes={"tb4": 66})
    metrics = asyncio.run(controller.validate(step=0))
    by_set = {call.get("set_name"): call for call in calls}
    assert set(by_set) == {None, "tb4", "probe"}
    assert (by_set["tb4"]["num_groups"], by_set["tb4"]["group_size"]) == (66, 5)
    assert by_set["tb4"]["sampling"] == by_set[None]["sampling"]
    assert by_set[None]["sampling"].temperature == 1.0
    probe = by_set["probe"]
    assert (probe["num_groups"], probe["group_size"]) == (3, 2)
    assert (probe["sampling"].temperature, probe["sampling"].top_p) == (0.0, 1.0)
    assert probe["sampling"].max_tokens == 7
    keys = [metric.key for metric in metrics]
    assert {"validation/valid", "validation/tb4/valid", "validation/probe/valid"} <= set(
        keys
    )
    assert keys.count("validation/policy_version") == 1


@pytest.mark.parametrize(
    "steps,expected",
    [
        ((0, 10, 20, 30, 40, 50), [0, 30, 50]),
        # The loop exited early (the batcher drained): the post-training pass is
        # labelled 37, not num_training_steps=50, and must still score the set.
        ((0, 10, 20, 30, 37), [0, 30, 37]),
    ],
)
def test_every_runs_a_set_on_every_nth_pass_and_always_on_the_final(steps, expected):
    validation = ValidationConfig(
        num_samples=89, interval=10, sets=[ValidationSetConfig(name="tb4", every=3)]
    )
    controller, calls = _controller(
        validation, num_training_steps=50, set_sizes={"tb4": 66}
    )
    scored = []
    for step in steps:
        calls.clear()
        final = step == steps[-1]
        asyncio.run(controller.validate(step=step, final=final))
        assert any("set_name" not in call for call in calls)  # main set every pass
        if any(call.get("set_name") == "tb4" for call in calls):
            scored.append(step)
    assert scored == expected


def test_validate_and_log_forwards_final_and_keeps_the_old_call_otherwise():
    from unittest.mock import AsyncMock, MagicMock

    controller = object.__new__(Controller)
    controller._trainer_policy_version = 37
    controller._validation_results = {}
    controller.metrics_processor = MagicMock()
    controller.validate = AsyncMock(return_value=[])
    asyncio.run(controller._validate_and_log(step=37, final=True))
    controller.validate.assert_awaited_once_with(step=37, final=True)
    controller.validate.reset_mock()
    asyncio.run(controller._validate_and_log(step=20))
    controller.validate.assert_awaited_once_with(step=20)


def test_the_post_training_pass_is_marked_final():
    """The post-training pass is labelled with the version reached, which can be
    below num_training_steps; it must still say final."""
    from unittest.mock import AsyncMock, MagicMock

    controller = object.__new__(Controller)
    controller._trainer_policy_version = 37
    controller.start_step = 0
    controller._validation_results = {0: {}}
    controller.eval_generator_router = None
    controller._await_pending_validation = AsyncMock()
    controller._validate_and_log = AsyncMock(return_value={})
    controller._log_reward_delta = MagicMock()
    asyncio.run(controller._post_training_validation())
    controller._validate_and_log.assert_awaited_once_with(step=37, final=True)


def test_a_pass_with_only_a_named_set():
    validation = ValidationConfig(
        num_samples=0, sets=[ValidationSetConfig(name="tb4", num_samples=4)]
    )
    controller, calls = _controller(validation)
    asyncio.run(controller.validate(step=0))
    assert [call.get("set_name") for call in calls] == ["tb4"]


# --------------------------------------------------------------------------- #
# Traces
# --------------------------------------------------------------------------- #
def test_a_named_set_writes_its_own_trace_report_with_the_set_on_each_row(tmp_path):
    recorder = ValidationTraceRecorder.Config(enable=True).build(dump_dir=str(tmp_path))
    groups = [_group(-1, [1.0, 0.0]), _group(-2, [0.0, 0.0])]
    recorder.record(
        policy_version=20, groups=groups, task_ids=["a", "b"], decode=lambda ids: ""
    )
    summary = recorder.record(
        policy_version=20,
        groups=groups,
        task_ids=["a", "b"],
        decode=lambda ids: "",
        set_name="tb4",
    )
    main_rows = json.loads(
        (tmp_path / "validation_traces/step-20/index.json").read_text()
    )
    set_rows = json.loads(
        (tmp_path / "validation_traces/tb4/step-20/index.json").read_text()
    )
    assert all("set" not in row for row in main_rows)
    assert [row["set"] for row in set_rows] == ["tb4"] * 4
    assert summary.report_dir.endswith("validation_traces/tb4/step-20")
    assert summary.pass_at_k == 0.5


def test_the_controller_records_a_named_set_with_its_own_shape(tmp_path):
    recorder = SimpleNamespace(
        validation_trace_recorder=ValidationTraceRecorder.Config(enable=True).build(
            dump_dir=str(tmp_path)
        ),
        config=SimpleNamespace(
            async_loop=SimpleNamespace(
                validation=SimpleNamespace(num_samples=89, group_size=5)
            )
        ),
        renderer=SimpleNamespace(_tokenizer=SimpleNamespace(decode=lambda ids: "")),
    )
    summary = Controller._record_validation_traces(
        recorder,
        step=0,
        samples=["a"],
        rollout_groups=[_group(-1, [1.0, 1.0])],
        set_name="tb4",
        num_samples=1,
        group_size=2,
    )
    assert summary.valid and summary.expected_num_trials == 2
    assert (tmp_path / "validation_traces/tb4/step-0/INDEX.md").exists()


# --------------------------------------------------------------------------- #
# tmax recipe: SWE_VAL_SETS and row-count defaults
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_val_env(monkeypatch):
    for name in ("SWE_VAL_SETS", "SWE_VAL_SAMPLES", "SWE_GPU_TRIAL_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", "/data/tb2.jsonl")


def test_without_swe_val_sets_nothing_changes(no_val_env):
    validation = tmax_config_registry._tmax_9b_validation()
    rollouter = tmax_config_registry._tmax_rollouter()
    assert validation.sets == [] and rollouter.validation_sets == {}
    # An unreadable main file keeps the historical 89.
    assert validation.num_samples == 89
    assert rollouter.gpu_trial_concurrency == 5


def test_main_set_defaults_to_its_row_count(no_val_env, tmp_path, monkeypatch):
    path = _write_rows(tmp_path / "tb.jsonl", [f"t{i}" for i in range(66)])
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", path)
    assert tmax_config_registry._tmax_9b_validation().num_samples == 66
    monkeypatch.setenv("SWE_VAL_SAMPLES", "89")
    warnings = []
    monkeypatch.setattr(
        tmax_config_registry.logger, "warning", lambda msg: warnings.append(msg)
    )
    assert tmax_config_registry._tmax_9b_validation().num_samples == 89
    assert "passes score different tasks" in warnings[0]


def test_swe_val_sets_builds_each_set_and_its_dataset(no_val_env, tmp_path, monkeypatch):
    tb4 = _write_rows(tmp_path / "tb4.jsonl", [f"t{i}" for i in range(66)])
    probe = _write_rows(tmp_path / "probe.jsonl", ["a", "b"])
    monkeypatch.setenv(
        "SWE_VAL_SETS", f" tb4={tb4} , probe={probe},gone={tmp_path}/missing.jsonl"
    )
    monkeypatch.setenv("SWE_VAL_SET_PROBE_K", "2")
    monkeypatch.setenv("SWE_VAL_SET_PROBE_TEMPERATURE", "0")
    monkeypatch.setenv("SWE_VAL_SET_PROBE_TOP_P", "0.5")
    monkeypatch.setenv("SWE_VAL_SET_PROBE_MAX_TOKENS", "4096")
    monkeypatch.setenv("SWE_VAL_SET_PROBE_SAMPLES", "1")
    monkeypatch.setenv("SWE_VAL_SET_TB4_EVERY", "2")
    sets = {s.name: s for s in tmax_config_registry._tmax_9b_validation().sets}
    assert sets["tb4"] == ValidationSetConfig(name="tb4", num_samples=66, every=2)
    assert sets["probe"] == ValidationSetConfig(
        name="probe", num_samples=1, group_size=2, temperature=0.0, top_p=0.5,
        max_tokens=4096,
    )
    # Not readable here: the controller takes the count from the dataset.
    assert sets["gone"].num_samples == -1
    datasets = tmax_config_registry._tmax_rollouter().validation_sets
    assert datasets["tb4"] == TMaxDataset.Config(
        data_path=tb4, seed=99, shuffle=False, holdout_n=0
    )
    assert list(datasets) == ["tb4", "probe", "gone"]


@pytest.mark.parametrize("spec", ["tb4", "=x.jsonl", "tb4=", "tb4=a,TB4=b"])
def test_malformed_swe_val_sets(no_val_env, monkeypatch, spec):
    monkeypatch.setenv("SWE_VAL_SETS", spec)
    with pytest.raises(ValueError):
        tmax_config_registry._tmax_9b_validation()


def test_every_tmax_recipe_carries_the_sets(no_val_env, tmp_path, monkeypatch):
    tb4 = _write_rows(tmp_path / "tb4.jsonl", ["a", "b", "c"])
    monkeypatch.setenv("SWE_VAL_SETS", f"tb4={tb4}")
    monkeypatch.setenv("SWE_GPU_TRIAL_CONCURRENCY", "3")
    for recipe in (
        tmax_config_registry.rl_grpo_qwen3_5_9b_tmax,
        tmax_config_registry.rl_grpo_qwen3_5_9b_tmax_tb2_eval,
        tmax_config_registry.rl_grpo_qwen3_5_27b_tmax,
    ):
        config = recipe()
        assert [s.name for s in config.async_loop.validation.sets] == ["tb4"]
        assert set(config.rollouter.validation_sets) == {"tb4"}
        assert config.rollouter.gpu_trial_concurrency == 3
        _check_validation_sets(
            config.async_loop.validation, datasets=config.rollouter.validation_sets
        )


def test_the_eval_recipe_scores_its_whole_file_by_default(no_val_env, tmp_path, monkeypatch):
    path = _write_rows(tmp_path / "tb.jsonl", [f"t{i}" for i in range(66)])
    monkeypatch.setattr(tmax_config_registry, "_TB2_DATA", path)
    config = tmax_config_registry.rl_grpo_qwen3_5_9b_tmax_tb2_eval()
    assert config.async_loop.validation.num_samples == 66


def test_validation_config_round_trips_through_replace():
    validation = ValidationConfig(sets=[ValidationSetConfig(name="tb4")])
    assert dataclasses.replace(validation, interval=0).sets == validation.sets


# --------------------------------------------------------------------------- #
# Dry run: two passes through the controller with a stubbed group runner
# --------------------------------------------------------------------------- #
def test_dry_run_two_passes_of_main_and_tb4(tmp_path, monkeypatch):
    """Real datasets, collector, trace recorder, metric logging and W&B mirror;
    only the group runner (sandbox + generator) is stubbed. A task "passes" when
    its id ends in 0."""
    import sys
    from unittest.mock import MagicMock

    fake_wandb = MagicMock()
    fake_wandb.run = object()
    monkeypatch.setitem(sys.modules, "wandb", fake_wandb)

    def dataset(name, ids):
        return TMaxDataset.Config(
            data_path=_write_rows(tmp_path / f"{name}.jsonl", ids),
            seed=99,
            shuffle=False,
            holdout_n=0,
        ).build()

    rollouter = object.__new__(Rollouter)
    rollouter._validation_dataset = dataset("tb2", ["m0", "m1", "m2"])
    rollouter._validation_sets = {"tb4": dataset("tb4", ["g0", "g1", "g2", "g3"])}

    async def run_group(*, sample, group_id, group_size, sampling, worker):
        assert sampling.temperature == (0.6 if sample.instance_id[0] == "g" else 1.0)
        await asyncio.sleep(0)
        reward = 1.0 if sample.instance_id.endswith("0") else 0.0
        return _group(group_id, [reward] * group_size)

    validation = ValidationConfig(
        num_samples=3,
        group_size=2,
        temperature=1.0,
        top_p=1.0,
        interval=40,
        trace=ValidationTraceRecorder.Config(enable=True),
        sets=[ValidationSetConfig(name="tb4", group_size=3, temperature=0.6)],
    )
    _check_validation_sets(validation, datasets=rollouter._validation_sets)
    controller = object.__new__(Controller)
    controller.config = SimpleNamespace(
        async_loop=SimpleNamespace(validation=validation, num_training_steps=40),
        num_eval_generators=0,
    )
    controller._sampling = SamplingConfig()
    controller._rollouter = rollouter
    controller._next_validation_group_id = -1
    controller._eval_rollout_workers = []
    controller._run_validation_group = run_group
    recorded = []
    controller.rollout_recorder = SimpleNamespace(
        record=lambda **kwargs: recorded.append(kwargs)
    )
    controller.validation_trace_recorder = validation.trace.build(dump_dir=str(tmp_path))
    controller.renderer = SimpleNamespace(_tokenizer=SimpleNamespace(decode=lambda ids: ""))
    controller.metrics_processor = MagicMock()
    controller._trainer_policy_version = 0
    controller._validation_results = {}

    for step in (0, 40):
        aggregated = asyncio.run(controller._validate_and_log(step=step))
        assert aggregated["validation_reward/_mean"] == pytest.approx(1 / 3)
        assert aggregated["validation/pass_at_k/mean"] == pytest.approx(1 / 3)
        assert aggregated["validation/valid"] == 1.0
        assert aggregated["validation/tb4/reward/_mean"] == 0.25
        assert aggregated["validation/tb4/pass_at_k/mean"] == 0.25
        assert aggregated["validation/tb4/valid"] == 1.0
        assert aggregated["validation/tb4/trace_pass_at_k"] == 0.25
        assert aggregated["validation/trace_pass_at_k"] == pytest.approx(1 / 3)
        rows = json.loads(
            (tmp_path / f"validation_traces/tb4/step-{step}/index.json").read_text()
        )
        # Every TB4 task, each once per pass, k=3 trials each, on both passes.
        assert sorted(row["task"] for row in rows) == sorted(["g0", "g1", "g2", "g3"] * 3)
        assert {row["set"] for row in rows} == {"tb4"}
        main_rows = json.loads(
            (tmp_path / f"validation_traces/step-{step}/index.json").read_text()
        )
        assert sorted(row["task"] for row in main_rows) == sorted(["m0", "m1", "m2"] * 2)
        mirrored = fake_wandb.log.call_args.args[0]
        assert mirrored["validation/tb4/pass_at_k/mean"] == 0.25
        assert mirrored["validation/policy_version"] == step
    assert len(recorded) == 4  # main + tb4, two passes
    group_ids = [
        group.group_id for call in recorded for group in call["rollout_groups"]
    ]
    assert len(group_ids) == len(set(group_ids)) == 14 and max(group_ids) < 0


def test_background_trace_reports_cover_every_set(tmp_path):
    """trace_report_in_background: each set's report is its own task, the pass
    logs no trace_pass_at_k, and every report still lands on disk."""
    validation = ValidationConfig(
        num_samples=2,
        group_size=2,
        trace_report_in_background=True,
        trace=ValidationTraceRecorder.Config(enable=True),
        sets=[ValidationSetConfig(name="tb4", num_samples=1)],
    )
    controller, _calls = _controller(validation)
    controller.validation_trace_recorder = validation.trace.build(dump_dir=str(tmp_path))
    controller.renderer = SimpleNamespace(_tokenizer=SimpleNamespace(decode=lambda ids: ""))
    controller._trace_report_tasks = set()

    async def collect(**kwargs):
        groups = [
            _group(-(i + 1), [1.0] * kwargs["group_size"])
            for i in range(kwargs["num_groups"])
        ]
        return [f"t{i}" for i in range(kwargs["num_groups"])], groups, []

    controller._collect_validation_rollouts = collect

    async def run():
        metrics = await controller.validate(step=5)
        names = sorted(task.get_name() for task in controller._trace_report_tasks)
        await asyncio.gather(*controller._trace_report_tasks)
        return metrics, names

    metrics, names = asyncio.run(run())
    assert names == ["validation_trace_5", "validation_trace_tb4_5"]
    assert not any("trace_pass_at_k" in metric.key for metric in metrics)
    assert (tmp_path / "validation_traces/step-5/index.json").exists()
    assert (tmp_path / "validation_traces/tb4/step-5/index.json").exists()


def test_one_failing_set_costs_only_itself():
    validation = ValidationConfig(
        num_samples=2,
        group_size=1,
        sets=[
            ValidationSetConfig(name="tb4", num_samples=1),
            ValidationSetConfig(name="probe", num_samples=1),
        ],
    )
    controller, _calls = _controller(validation)
    recorded = []
    controller.rollout_recorder = SimpleNamespace(
        record=lambda **kwargs: recorded.append(kwargs.get("set_name"))
    )

    async def collect(**kwargs):
        name = kwargs.get("set_name")
        if name == "tb4":
            await asyncio.sleep(0)
            raise RuntimeError("tb4 dataset broke")
        await asyncio.sleep(0.01)  # still running when tb4 fails
        prefix = "validation" if name is None else f"validation/{name}"
        return [], [], [m.Metric(f"{prefix}/valid", m.NoReduce(1.0))]

    controller._collect_validation_rollouts = collect
    metrics = asyncio.run(controller.validate(step=0))
    values = {
        metric.key: metric.value.value
        for metric in metrics
        if metric.key.endswith("/valid")
    }
    assert values == {
        "validation/valid": 1.0,
        "validation/tb4/valid": 0.0,
        "validation/probe/valid": 1.0,
    }
    assert recorded == [None, "probe"]


def test_a_lone_main_set_failure_still_raises():
    validation = ValidationConfig(num_samples=2)
    controller, _calls = _controller(validation)

    async def collect(**kwargs):
        raise RuntimeError("main broke")

    controller._collect_validation_rollouts = collect
    with pytest.raises(RuntimeError, match="main broke"):
        asyncio.run(controller.validate(step=0))


def test_rollout_samples_carry_the_set_label(tmp_path):
    from torchtitan.experiments.rl.rollout_recorder import RolloutSampleRecorder

    recorder = RolloutSampleRecorder.Config().build(dump_dir=str(tmp_path))
    recorder.record(is_validation=True, rollout_groups=[_group(-1, [1.0])])
    recorder.record(
        is_validation=True, rollout_groups=[_group(-2, [0.0])], set_name="tb4"
    )
    lines = [
        json.loads(line)
        for path in tmp_path.rglob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert [line.get("validation_set") for line in lines] == [None, "tb4"]
    assert "validation_set" not in lines[0]


def test_eval_recipe_without_swe_tb2_data_keeps_89(no_val_env, tmp_path, monkeypatch):
    """Without SWE_TB2_DATA the eval recipe reads the training mix; its row count
    (15k for a real mix) is not an eval size."""
    mix = _write_rows(tmp_path / "mix.jsonl", [f"t{i}" for i in range(200)])
    monkeypatch.setattr(tmax_config_registry, "_TB2_DATA", "")
    monkeypatch.setattr(tmax_config_registry, "_DEFAULT_DATA", mix)
    config = tmax_config_registry.rl_grpo_qwen3_5_9b_tmax_tb2_eval()
    assert config.async_loop.validation.num_samples == 89


def test_a_comma_in_a_set_path_is_a_clear_error(no_val_env, monkeypatch):
    monkeypatch.setenv("SWE_VAL_SETS", "tb4=/data/a,b.jsonl")
    with pytest.raises(ValueError, match="cannot contain a comma"):
        tmax_config_registry._tmax_9b_validation()


def test_empty_swe_val_samples_means_unset(no_val_env, monkeypatch):
    monkeypatch.setenv("SWE_VAL_SAMPLES", "")
    monkeypatch.setattr(tmax_config_registry, "_TB2_VAL_DATA", "")
    monkeypatch.setenv("SWE_HOLDOUT_N", "8")
    assert tmax_config_registry._tmax_rollouter().train_dataset.holdout_n == 8
    assert tmax_config_registry._tmax_9b_validation().num_samples == 32
    monkeypatch.setenv("SWE_HOLDOUT_N", "0")
    with pytest.raises(ValueError, match="requires a separate SWE_TB2_VAL_DATA"):
        tmax_config_registry._tmax_rollouter()


def test_a_failing_named_set_leaves_no_collection_running():
    """The reviewer's probe at 492a3a59: tb4's collection raised, validate()
    re-raised while the main collection kept running unawaited, so a run_async
    pass counted as done under orphaned trials."""
    validation = ValidationConfig(
        num_samples=2,
        sets=[ValidationSetConfig(name="tb4", num_samples=1)],
        trace_report_in_background=True,
    )
    controller, _calls = _controller(validation, set_sizes={"tb4": 3})
    controller._trace_report_tasks = set()
    state = {"main_done": False}

    async def collect(**kwargs):
        if kwargs.get("set_name") == "tb4":
            raise RuntimeError("tb4 collection failed")
        await asyncio.sleep(0.2)
        state["main_done"] = True
        return [], [], [m.Metric("validation/valid", m.NoReduce(1.0))]

    controller._collect_validation_rollouts = collect

    async def run():
        metrics = await controller.validate(step=5)
        # The main set's background trace report is tracked and drained at the
        # end of the run; anything else still running would be orphaned.
        orphans = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task not in controller._trace_report_tasks
        ]
        await controller._drain_trace_reports()
        return metrics, orphans

    metrics, orphans = asyncio.run(run())
    assert orphans == [] and state["main_done"]
    valid = {
        metric.key: metric.value.value
        for metric in metrics
        if metric.key.endswith("/valid")
    }
    assert valid == {"validation/valid": 1.0, "validation/tb4/valid": 0.0}


def _slow_report(controller, state, name):
    async def report():
        await asyncio.sleep(0.05)
        state.append(name)

    task = asyncio.ensure_future(report())
    controller._trace_report_tasks.add(task)
    task.add_done_callback(controller._trace_report_tasks.discard)


def test_close_drains_trace_reports_after_the_actors_are_down():
    """The last pass's background reports are written at the end of close(), once
    the trainer and generators are released, not while they wait."""
    from unittest.mock import MagicMock

    controller = object.__new__(Controller)
    controller._validation_task = None
    controller.trainer = None
    controller.generator_router = None
    controller.eval_generator_router = None
    controller.metrics_processor = MagicMock()
    controller._trace_report_tasks = set()
    events = []

    class _Mesh:
        async def stop(self):
            events.append("mesh stopped")

    controller._proc_meshes = [_Mesh()]

    async def run():
        _slow_report(controller, events, "main report written")
        _slow_report(controller, events, "tb4 report written")
        await controller.close()

    asyncio.run(run())
    assert events == ["mesh stopped", "main report written", "tb4 report written"]
    assert not controller._trace_report_tasks


def test_the_eval_only_pass_leaves_its_reports_to_close():
    from unittest.mock import AsyncMock

    controller = object.__new__(Controller)
    controller.config = SimpleNamespace(
        training_heartbeat_interval_sec=0,
        async_loop=SimpleNamespace(
            num_training_steps=0, validation=ValidationConfig(run_async=False)
        ),
    )
    controller.start_step = 0
    controller._trace_report_tasks = set()
    written = []

    async def validate_and_log(**kwargs):
        _slow_report(controller, written, "main")
        return {}

    controller._validate_and_log = AsyncMock(side_effect=validate_and_log)

    async def run():
        await controller.run()
        pending = len(controller._trace_report_tasks)
        await controller._drain_trace_reports()
        return pending

    assert asyncio.run(run()) == 1 and written == ["main"]


def test_a_childs_cancellation_is_reraised():
    """gather(return_exceptions=True) hands back a child's CancelledError as a
    value; unpacking it would turn a cancellation into a TypeError."""
    validation = ValidationConfig(
        num_samples=2, sets=[ValidationSetConfig(name="tb4", num_samples=1)]
    )
    controller, _calls = _controller(validation)

    async def collect(**kwargs):
        if kwargs.get("set_name") is None:
            raise asyncio.CancelledError()
        return [], [], []

    controller._collect_validation_rollouts = collect
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(controller.validate(step=5))


def test_cancelling_a_multi_set_pass_cancels_every_collection():
    validation = ValidationConfig(
        num_samples=2, sets=[ValidationSetConfig(name="tb4", num_samples=1)]
    )
    controller, _calls = _controller(validation)
    cancelled = []

    async def collect(**kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(kwargs.get("set_name"))
            raise

    controller._collect_validation_rollouts = collect

    async def run():
        task = asyncio.create_task(controller.validate(step=5))
        await asyncio.sleep(0.05)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        orphans = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        return result, orphans

    result, orphans = asyncio.run(run())
    assert isinstance(result[0], asyncio.CancelledError) and orphans == []
    assert sorted(cancelled, key=str) == [None, "tb4"]


@pytest.mark.parametrize("tb4_due", [True, False])
def test_a_main_set_error_raises_only_when_no_named_set_is_due(tb4_due):
    """With a named set due, the main set's error is logged (validation/valid=0)
    and the pass goes on; with none due it raises, as it always has."""
    validation = ValidationConfig(
        num_samples=2, sets=[ValidationSetConfig(name="tb4", num_samples=1, every=3)]
    )
    controller, _calls = _controller(validation)
    controller._validation_pass_index = 0 if tb4_due else 1

    async def collect(**kwargs):
        if kwargs.get("set_name") is None:
            raise RuntimeError("main broke")
        return [], [], [m.Metric("validation/tb4/valid", m.NoReduce(1.0))]

    controller._collect_validation_rollouts = collect
    if not tb4_due:
        with pytest.raises(RuntimeError, match="main broke"):
            asyncio.run(controller.validate(step=10))
        return
    metrics = asyncio.run(controller.validate(step=10))
    valid = {
        metric.key: metric.value.value
        for metric in metrics
        if metric.key.endswith("/valid")
    }
    assert valid == {"validation/valid": 0.0, "validation/tb4/valid": 1.0}


def test_the_tmax_rollouter_draws_named_sets(tmp_path):
    """TMaxRollouter overrides get_validation_sample; the override must take the
    set name, or every named-set draw raises and the set is never scored."""
    from torchtitan.experiments.rl.examples.tmax.rollouter import TMaxRollouter

    def dataset(name, ids):
        return TMaxDataset.Config(
            data_path=_write_rows(tmp_path / f"{name}.jsonl", ids),
            seed=99,
            shuffle=False,
            holdout_n=0,
        ).build()

    rollouter = object.__new__(TMaxRollouter)
    rollouter._validation_dataset = dataset("tb2", ["m0"])
    rollouter._validation_sets = {"tb4": dataset("tb4", ["g0", "g1"])}
    assert rollouter.get_validation_sample().instance_id == "m0"
    assert rollouter.get_validation_sample("tb4").instance_id == "g0"
