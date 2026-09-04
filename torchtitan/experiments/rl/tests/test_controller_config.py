# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl.controller import Controller, ValidationConfig

from torchtitan.experiments.rl.models.vllm_registry import InferenceParallelismConfig


def _config(**overrides) -> SimpleNamespace:
    """The scalar fields Controller.Config.__post_init__ reaches before its first
    raise, so a test can exercise one check without building the whole config."""
    return SimpleNamespace(
        **{
            "num_generators": 1,
            "num_eval_generators": 0,
            "num_eval_rollout_workers": 0,
            "eval_rollout_concurrency": 0,
            "eval_generator_data_parallel_degree": 0,
            "torchstore_reset_interval": 0,
            **overrides,
        }
    )


def test_torchstore_transport_recycle_is_rejected():
    with pytest.raises(ValueError, match="torchstore_reset_interval must be 0"):
        Controller.Config.__post_init__(_config(torchstore_reset_interval=32))


def test_torchstore_volume_placement_is_validated():
    with pytest.raises(ValueError, match="torchstore_volume_placement"):
        Controller.Config.__post_init__(_config(torchstore_volume_placement="worker"))


def test_eval_rollout_workers_require_an_eval_generator():
    with pytest.raises(ValueError, match="num_eval_rollout_workers requires"):
        Controller.Config.__post_init__(
            _config(num_eval_generators=0, num_eval_rollout_workers=4)
        )


def test_negative_eval_generators_is_rejected():
    with pytest.raises(ValueError, match="num_eval_generators must be non-negative"):
        Controller.Config.__post_init__(_config(num_eval_generators=-1))


def test_negative_eval_generator_dp_is_rejected():
    with pytest.raises(
        ValueError, match="eval_generator_data_parallel_degree must be non-negative"
    ):
        Controller.Config.__post_init__(_config(eval_generator_data_parallel_degree=-1))


def test_eval_generator_dp_without_eval_generators_warns(caplog):
    """It is a no-op, not an error -- but a silent one leaves the GPU count wrong."""
    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError, match="torchstore_volume_placement"):
            # The next check after ours; reaching it means we did not raise.
            Controller.Config.__post_init__(
                _config(
                    eval_generator_data_parallel_degree=1,
                    num_eval_generators=0,
                    torchstore_volume_placement="worker",
                )
            )
    assert "has no effect with num_eval_generators=0" in caplog.text


def test_eval_generator_parallelism_defaults_to_the_training_generator():
    """The eval mesh's width is decided in ONE place; train.py and setup_async
    both read it, and a disagreement spawns an engine onto a mesh of another size."""
    parallelism = InferenceParallelismConfig(
        data_parallel_degree=4, tensor_parallel_degree=2
    )
    config = SimpleNamespace(
        generator=SimpleNamespace(parallelism=parallelism),
        eval_generator_data_parallel_degree=0,
    )
    assert Controller.Config.eval_generator_parallelism(config) is parallelism

    config.eval_generator_data_parallel_degree = 1
    resolved = Controller.Config.eval_generator_parallelism(config)
    assert resolved.data_parallel_degree == 1
    # TP is the training generator's: a model that needs TP to fit still needs it.
    assert resolved.tensor_parallel_degree == 2
    assert parallelism.data_parallel_degree == 4, "must not mutate the shared config"


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"group_size": 0}, "validation.group_size must be positive"),
        ({"num_samples": -1}, "validation.num_samples must be non-negative"),
    ],
)
def test_validation_config_is_validated(kwargs, match):
    with pytest.raises(ValueError, match=match):
        ValidationConfig(**kwargs)
