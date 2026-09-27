# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Roles placed on a shared host mesh draw disjoint GPUs.

With caller-provided host meshes every role used to get its own GPU allocator
starting at GPU 0, so seven generators and an eval generator placed on one
remote node would all have been pinned to GPU 0.
"""

from __future__ import annotations

import os

import pytest

from torchtitan.experiments.rl import train


class _FakeHostMesh:
    """One host; records each spawn's bootstrap so the test can run it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.spawns: list[tuple[dict, object]] = []

    def __len__(self) -> int:
        return 1

    def spawn_procs(self, *, per_host, bootstrap):
        self.spawns.append((per_host, bootstrap))
        return (self.name, per_host["gpus"], bootstrap)


def _visible_devices(bootstrap, monkeypatch) -> str:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "unset")
    bootstrap()
    return os.environ["CUDA_VISIBLE_DEVICES"]


@pytest.fixture(autouse=True)
def _no_gpu_env(monkeypatch):
    monkeypatch.delenv("RL_GPUS", raising=False)
    monkeypatch.delenv("RL_GPU_OFFSET", raising=False)


def test_generators_on_one_host_get_disjoint_gpus(monkeypatch):
    trainer_host, generator_host = _FakeHostMesh("t"), _FakeHostMesh("g")
    trainer, generators, evals = train.spawn_proc_mesh(
        8,
        1,
        train.HostMeshes(
            trainer=trainer_host,
            generators=[generator_host] * 7,
            gpus_per_node=8,
            eval_generators=[generator_host],
        ),
        num_generators=7,
        num_eval_generators=1,
        per_eval_generator_world_size=1,
    )
    assert _visible_devices(trainer[2], monkeypatch) == "0,1,2,3,4,5,6,7"
    gen_gpus = [_visible_devices(g[2], monkeypatch) for g in generators]
    eval_gpus = [_visible_devices(e[2], monkeypatch) for e in evals]
    assert gen_gpus + eval_gpus == [str(i) for i in range(8)]


def test_overcommitting_a_shared_host_is_refused():
    host = _FakeHostMesh("g")
    with pytest.raises(RuntimeError, match="Requested"):
        train.spawn_proc_mesh(
            1,
            1,
            train.HostMeshes(
                trainer=_FakeHostMesh("t"),
                generators=[host] * 9,
                gpus_per_node=8,
            ),
            num_generators=9,
        )


def test_no_addresses_means_single_node(monkeypatch):
    monkeypatch.delenv("RL_TRAINER_HOST_ADDR", raising=False)
    monkeypatch.delenv("RL_GENERATOR_HOST_ADDR", raising=False)
    assert train._attached_host_meshes(num_generators=5, num_eval_generators=1) is None


def test_one_address_alone_is_an_error(monkeypatch):
    monkeypatch.setenv("RL_TRAINER_HOST_ADDR", "tcp://a:22222")
    monkeypatch.delenv("RL_GENERATOR_HOST_ADDR", raising=False)
    with pytest.raises(ValueError, match="both"):
        train._attached_host_meshes(num_generators=5, num_eval_generators=1)
