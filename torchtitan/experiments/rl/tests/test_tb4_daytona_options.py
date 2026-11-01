# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""TB4's opt-in DaytonaSandbox options, and that leaving them out changes nothing."""

from __future__ import annotations

import asyncio

import pytest

from torchtitan.experiments.rl.harness.sandbox import daytona as daytona_mod
from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox


class _Captured(Exception):
    pass


class _FakeClient:
    def __init__(self):
        self.params = None

    async def create(self, params, timeout=None):
        self.params = params
        raise _Captured


def _create(monkeypatch, sandbox: DaytonaSandbox, **env):
    client = _FakeClient()
    seen = {}

    async def fake_get_shared_client(**kwargs):
        seen.update(kwargs)
        return client

    monkeypatch.setattr(daytona_mod, "_get_shared_client", fake_get_shared_client)
    for key in (
        "TT_DAYTONA_EPHEMERAL",
        "TT_DAYTONA_TTL_MIN",
        "TT_DAYTONA_MAX_MEM_GB",
        "TT_DAYTONA_MAX_CPU",
        "TT_DAYTONA_MAX_DISK_GB",
        "TT_DAYTONA_GPU_MAX_CPU",
        "TT_DAYTONA_GPU_MAX_MEM_GB",
        "TT_DAYTONA_GPU_MAX_DISK_GB",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DAYTONA_API_KEY", "k")
    monkeypatch.setenv("TT_DAYTONA_CREATE_RETRIES", "0")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(_Captured):
        asyncio.run(sandbox.__aenter__())
    return client.params, seen


def test_default_request_is_unchanged(monkeypatch):
    params, seen = _create(monkeypatch, DaytonaSandbox("alpine:3.19", cpu=2, memory=4))
    assert params.image == "alpine:3.19"
    assert (params.resources.cpu, params.resources.memory) == (2, 4)
    assert params.resources.gpu is None and params.resources.gpu_type is None
    assert params.network_block_all is None
    assert params.ephemeral is None
    assert "own_target" not in seen


def test_gpu_rows_ask_for_the_gpu_ephemerally_in_their_region(monkeypatch):
    sb = DaytonaSandbox("img", gpu=1, gpu_type=["H100"], target="earth")
    params, seen = _create(monkeypatch, sb)
    assert params.resources.gpu == 1
    assert [g.value for g in params.resources.gpu_type] == ["H100"]
    assert params.ephemeral is True
    assert seen["own_target"] == "earth"


def test_network_block_all(monkeypatch):
    params, _seen = _create(monkeypatch, DaytonaSandbox("img", network_block_all=True))
    assert params.network_block_all is True


def test_cpu_rows_keep_the_cpu_clamp(monkeypatch):
    # #242's clamp: 4 vCPU / 8 GiB / 20 GiB (disk was 10); a request above is shrunk.
    params, _seen = _create(
        monkeypatch, DaytonaSandbox("img", cpu=16, memory=32, disk_gb=1000)
    )
    r = params.resources
    assert (r.cpu, r.memory, r.disk) == (4, 8, 20)


@pytest.mark.parametrize(
    "request_, granted",
    [
        ((8, 16, 30), (8, 16, 30)),  # math-eval-grader: within the GPU caps
        ((16, 32, 1000), (16, 32, 512)),  # jax-speedrun-gpu: disk clamped
        ((32, 256, 30), (16, 192, 30)),
    ],
)
def test_gpu_rows_clamp_to_the_gpu_caps(monkeypatch, request_, granted):
    cpu, mem, disk = request_
    sb = DaytonaSandbox(
        "img", cpu=cpu, memory=mem, disk_gb=disk, gpu=1, gpu_type=["H100"]
    )
    params, _seen = _create(monkeypatch, sb)
    r = params.resources
    assert (r.cpu, r.memory, r.disk) == granted
    # The GPU caps are their own knobs; the CPU ones do not touch a GPU row.
    params, _seen = _create(
        monkeypatch,
        DaytonaSandbox("img", cpu=8, memory=16, disk_gb=30, gpu=1),
        TT_DAYTONA_MAX_CPU="1",
        TT_DAYTONA_GPU_MAX_DISK_GB="20",
    )
    assert (params.resources.cpu, params.resources.disk) == (8, 20)


def test_option_validation():
    with pytest.raises(ValueError, match="gpu_type"):
        DaytonaSandbox("img", gpu_type=["H100"])
    with pytest.raises(ValueError, match="gpu must be a positive integer"):
        DaytonaSandbox("img", gpu=0)


def test_target_clients_are_cached_per_region(monkeypatch):
    created = []

    class _Async:
        def __init__(self, cfg):
            created.append(cfg.target)

    import daytona

    monkeypatch.setattr(daytona, "AsyncDaytona", _Async)
    monkeypatch.setattr(daytona_mod, "_TARGET_CLIENTS", {})
    monkeypatch.setattr(daytona_mod, "_SHARED_CLIENT", None)
    monkeypatch.setattr(daytona_mod, "_SHARED_CLIENT_LOCK", None)

    async def go():
        a = await daytona_mod._get_shared_client(api_key="k", api_url=None, target=None)
        b = await daytona_mod._get_shared_client(
            api_key="k", api_url=None, target=None, own_target="earth"
        )
        c = await daytona_mod._get_shared_client(
            api_key="k", api_url=None, target=None, own_target="earth"
        )
        d = await daytona_mod._get_shared_client(api_key="k", api_url=None, target="us")
        return a, b, c, d

    a, b, c, d = asyncio.run(go())
    assert b is c and a is d and a is not b
    assert created == [None, "earth"]
