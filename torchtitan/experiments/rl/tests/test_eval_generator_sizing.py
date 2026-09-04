# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""An eval generator does not have to be as wide as a training generator.

``num_eval_generators`` used to spawn each eval mesh at the training generator's
world size. On a multi-host run that is the right default; on ONE 8-GPU box it
means a host that only works every ``validation.interval`` steps takes as many
GPUs as the one feeding the rollout pool continuously -- with
``SWE_GEN_DP=4`` a single eval generator wanted 4 of the 8 cards.

``eval_generator_data_parallel_degree`` sizes it separately, so 3 trainer +
4 generator + 1 eval fits on 8. What the tests below pin is the GPU arithmetic:
the total, and the ORDER, since ``RL_GPUS`` is sliced positionally and a
mis-sized eval mesh silently shifts every device after it.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl import train
from torchtitan.experiments.rl.controller import Controller
from torchtitan.experiments.rl.models.vllm_registry import InferenceParallelismConfig


class _FakeHostMesh:
    """Records the size of each mesh spawned on it, in spawn order."""

    def __init__(self) -> None:
        self.spawned: list[tuple[int, list[str]]] = []

    def spawn_procs(self, *, per_host, bootstrap):
        num_gpus = per_host["gpus"]
        # The bootstrap runs in the spawned process and is what actually pins the
        # devices, so call it and read back what it would have set there.
        before = os.environ.get("CUDA_VISIBLE_DEVICES")
        try:
            bootstrap()
            devices = os.environ["CUDA_VISIBLE_DEVICES"].split(",")
        finally:
            if before is None:
                os.environ.pop("CUDA_VISIBLE_DEVICES", None)
            else:
                os.environ["CUDA_VISIBLE_DEVICES"] = before
        self.spawned.append((num_gpus, devices))
        return f"mesh_{len(self.spawned)}"


@pytest.fixture
def host(monkeypatch):
    mesh = _FakeHostMesh()
    monkeypatch.setattr(train, "this_host", lambda: mesh)
    return mesh


def test_eval_generator_can_be_narrower_than_a_training_generator(host, monkeypatch):
    """3 trainer + 4 generator + 1 eval = the 8 cards on one box."""
    monkeypatch.setenv("RL_GPUS", "0,1,2,3,4,5,6,7")

    train.spawn_proc_mesh(
        trainer_world_size=3,
        per_generator_world_size=4,
        num_generators=1,
        num_eval_generators=1,
        per_eval_generator_world_size=1,
    )

    assert [n for n, _ in host.spawned] == [3, 4, 1]
    # Order is placement: trainer first, then generators, then eval. RL_GPUS is
    # sliced by position, so a wrong eval size moves every device after it.
    assert [d for _, d in host.spawned] == [
        ["0", "1", "2"],
        ["3", "4", "5", "6"],
        ["7"],
    ]


def test_eval_generator_defaults_to_the_training_generator_width(host, monkeypatch):
    """Unset means the old behavior, which is what a multi-host run wants."""
    monkeypatch.setenv("RL_GPUS", "0,1,2,3,4,5,6,7")

    train.spawn_proc_mesh(
        trainer_world_size=2,
        per_generator_world_size=3,
        num_generators=1,
        num_eval_generators=1,
    )

    assert [n for n, _ in host.spawned] == [2, 3, 3]


def test_undersized_rl_gpus_is_rejected_before_anything_spawns(host, monkeypatch):
    """The eval mesh counts toward the total the provisioner demands."""
    monkeypatch.setenv("RL_GPUS", "0,1,2,3,4,5,6")

    with pytest.raises(ValueError, match="RL_GPUS lists 7 device"):
        train.spawn_proc_mesh(
            trainer_world_size=3,
            per_generator_world_size=4,
            num_generators=1,
            num_eval_generators=1,
            per_eval_generator_world_size=1,
        )
    assert host.spawned == []


def test_train_sizes_the_eval_mesh_from_the_config(monkeypatch):
    """main() and setup_async must agree on the width, so both read the same
    Config method rather than deriving it from generator.parallelism twice."""
    config = SimpleNamespace(
        generator=SimpleNamespace(
            parallelism=InferenceParallelismConfig(
                data_parallel_degree=4, tensor_parallel_degree=1
            )
        ),
        eval_generator_data_parallel_degree=1,
    )
    parallelism = Controller.Config.eval_generator_parallelism(config)
    assert train._compute_generator_world_size(parallelism) == 1
