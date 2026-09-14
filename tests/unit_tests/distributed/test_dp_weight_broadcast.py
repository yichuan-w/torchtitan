# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import torch

from torchtitan.distributed import dp_weight_broadcast as weight_broadcast
from torchtitan.distributed.dp_weight_broadcast import (
    dp_weight_broadcast_unsupported_reason,
    DPWeightBroadcaster,
)


class _Collectives:
    def __init__(
        self,
        *,
        metadata_mismatch: bool = False,
        fail_all_reduce_call: int | None = None,
    ) -> None:
        self.metadata_mismatch = metadata_mismatch
        self.fail_all_reduce_call = fail_all_reduce_call
        self.all_reduce_calls = 0
        self.remote_failure_active = False
        self.broadcasts: list[tuple[object, list[torch.Tensor], int, int]] = []

    def get_world_size(self, group) -> int:
        return group.world_size

    def get_rank(self) -> int:
        return 0

    def all_gather_object(self, output, value, *, group) -> None:
        for index in range(group.world_size):
            output[index] = value
        if self.metadata_mismatch and group.name == "dp_cpu":
            output[-1] = (("different",),)
        if self.remote_failure_active and group.name == "control":
            output[-1] = (1, "ValueError: store unavailable")
            self.remote_failure_active = False

    def all_reduce(self, tensor, *, op, group, async_op=False) -> None:
        assert async_op is False
        self.all_reduce_calls += 1
        if self.all_reduce_calls == self.fail_all_reduce_call:
            tensor.fill_(1)
            self.remote_failure_active = True

    def broadcast_coalesced(
        self, group, tensors: list[torch.Tensor], bucket_bytes: int, source: int
    ) -> None:
        self.broadcasts.append((group, tensors, bucket_bytes, source))


def _group(*, rank_in_group: int = 0, world_size: int = 2):
    return SimpleNamespace(
        rank_in_group=rank_in_group,
        world_size=world_size,
        ranks=list(range(world_size)),
        cpu_group=SimpleNamespace(name="dp_cpu", world_size=world_size),
        device_group=SimpleNamespace(name="dp_device", world_size=world_size),
    )


def _control_group(*, world_size: int = 2):
    return SimpleNamespace(name="control", world_size=world_size)


def _patch_collectives(monkeypatch, collectives: _Collectives) -> None:
    monkeypatch.setattr(
        weight_broadcast.dist, "get_world_size", collectives.get_world_size
    )
    monkeypatch.setattr(weight_broadcast.dist, "get_rank", collectives.get_rank)
    monkeypatch.setattr(weight_broadcast.dist, "all_reduce", collectives.all_reduce)
    monkeypatch.setattr(
        weight_broadcast.dist, "all_gather_object", collectives.all_gather_object
    )
    monkeypatch.setattr(
        weight_broadcast.dist,
        "_broadcast_coalesced",
        collectives.broadcast_coalesced,
    )


@pytest.mark.parametrize(
    ("dp_degree", "ep_degree", "expected"),
    [
        (1, 1, "data_parallel_degree must be greater than 1"),
        (4, 8, "expert-parallel weights are not replicated across DP ranks"),
        (4, 1, None),
    ],
)
def test_unsupported_reason(dp_degree, ep_degree, expected) -> None:
    assert (
        dp_weight_broadcast_unsupported_reason(
            data_parallel_degree=dp_degree,
            expert_parallel_degree=ep_degree,
        )
        == expected
    )


def test_source_fetches_then_broadcasts_and_applies(monkeypatch) -> None:
    collectives = _Collectives()
    _patch_collectives(monkeypatch, collectives)
    broadcaster = DPWeightBroadcaster(
        dp_group=_group(rank_in_group=0),
        control_group=_control_group(),
        expected_dp_degree=2,
    )
    weight = torch.zeros(2)
    state_dict: dict[str, object] = {
        "weight": weight,
        "buffer": torch.zeros(1),
        "extra": "unchanged",
    }
    events: list[str] = []

    async def fetch() -> None:
        events.append("fetch")
        weight.fill_(3)

    def apply() -> None:
        events.append("apply")

    asyncio.run(
        broadcaster.synchronize(
            state_dict=state_dict,
            fetch_from_store=fetch,
            apply_state_dict=apply,
        )
    )

    assert events == ["fetch", "apply"]
    assert weight.tolist() == [3, 3]
    assert len(collectives.broadcasts) == 1
    _, tensors, _, source = collectives.broadcasts[0]
    assert [tensor.numel() for tensor in tensors] == [1, 2]
    assert source == 0


def test_non_source_skips_store_fetch(monkeypatch) -> None:
    collectives = _Collectives()
    _patch_collectives(monkeypatch, collectives)
    broadcaster = DPWeightBroadcaster(
        dp_group=_group(rank_in_group=1),
        control_group=_control_group(),
        expected_dp_degree=2,
    )
    events: list[str] = []

    async def fetch() -> None:
        events.append("fetch")

    def apply() -> None:
        events.append("apply")

    asyncio.run(
        broadcaster.synchronize(
            state_dict={"weight": torch.zeros(2)},
            fetch_from_store=fetch,
            apply_state_dict=apply,
        )
    )

    assert events == ["apply"]
    assert len(collectives.broadcasts) == 1


def test_non_source_stops_when_store_reader_fails(monkeypatch) -> None:
    # Validation uses the first two status all-reduces; the third is the fetch.
    collectives = _Collectives(fail_all_reduce_call=3)
    _patch_collectives(monkeypatch, collectives)
    broadcaster = DPWeightBroadcaster(
        dp_group=_group(rank_in_group=1),
        control_group=_control_group(),
        expected_dp_degree=2,
    )
    events: list[str] = []

    async def fetch() -> None:
        events.append("fetch")

    with pytest.raises(RuntimeError, match="store fetch"):
        asyncio.run(
            broadcaster.synchronize(
                state_dict={"weight": torch.zeros(2)},
                fetch_from_store=fetch,
                apply_state_dict=lambda: events.append("apply"),
            )
        )

    assert events == []
    assert collectives.broadcasts == []


def test_metadata_mismatch_fails_before_fetch(monkeypatch) -> None:
    collectives = _Collectives(metadata_mismatch=True)
    _patch_collectives(monkeypatch, collectives)
    broadcaster = DPWeightBroadcaster(
        dp_group=_group(),
        control_group=_control_group(),
        expected_dp_degree=2,
    )
    events: list[str] = []

    async def fetch() -> None:
        events.append("fetch")

    with pytest.raises(RuntimeError, match="payload validation"):
        asyncio.run(
            broadcaster.synchronize(
                state_dict={"weight": torch.zeros(2)},
                fetch_from_store=fetch,
                apply_state_dict=lambda: events.append("apply"),
            )
        )

    assert events == []
    assert collectives.broadcasts == []


def test_empty_payload_fails_before_fetch(monkeypatch) -> None:
    collectives = _Collectives()
    _patch_collectives(monkeypatch, collectives)
    broadcaster = DPWeightBroadcaster(
        dp_group=_group(),
        control_group=_control_group(),
        expected_dp_degree=2,
    )
    fetched = False

    async def fetch() -> None:
        nonlocal fetched
        fetched = True

    with pytest.raises(RuntimeError, match="payload metadata"):
        asyncio.run(
            broadcaster.synchronize(
                state_dict={},
                fetch_from_store=fetch,
                apply_state_dict=lambda: None,
            )
        )

    assert not fetched
    assert collectives.broadcasts == []


def test_source_failure_skips_broadcast_and_apply(monkeypatch) -> None:
    collectives = _Collectives()
    _patch_collectives(monkeypatch, collectives)
    broadcaster = DPWeightBroadcaster(
        dp_group=_group(),
        control_group=_control_group(),
        expected_dp_degree=2,
    )
    applied = False

    async def fetch() -> None:
        raise ValueError("store unavailable")

    def apply() -> None:
        nonlocal applied
        applied = True

    with pytest.raises(RuntimeError, match="store fetch"):
        asyncio.run(
            broadcaster.synchronize(
                state_dict={"weight": torch.zeros(2)},
                fetch_from_store=fetch,
                apply_state_dict=apply,
            )
        )

    assert not applied
    assert collectives.broadcasts == []


def test_payload_change_during_fetch_skips_broadcast_and_apply(monkeypatch) -> None:
    collectives = _Collectives()
    _patch_collectives(monkeypatch, collectives)
    broadcaster = DPWeightBroadcaster(
        dp_group=_group(),
        control_group=_control_group(),
        expected_dp_degree=2,
    )
    state_dict: dict[str, object] = {"weight": torch.zeros(2)}
    applied = False

    async def fetch() -> None:
        state_dict["weight"] = torch.zeros(3)

    def apply() -> None:
        nonlocal applied
        applied = True

    with pytest.raises(RuntimeError, match="post-fetch payload validation"):
        asyncio.run(
            broadcaster.synchronize(
                state_dict=state_dict,
                fetch_from_store=fetch,
                apply_state_dict=apply,
            )
        )

    assert not applied
    assert collectives.broadcasts == []


def test_apply_failure_is_coordinated(monkeypatch) -> None:
    collectives = _Collectives()
    _patch_collectives(monkeypatch, collectives)
    broadcaster = DPWeightBroadcaster(
        dp_group=_group(),
        control_group=_control_group(),
        expected_dp_degree=2,
    )

    async def fetch() -> None:
        return None

    def apply() -> None:
        raise ValueError("load failed")

    with pytest.raises(RuntimeError, match="state-dict apply"):
        asyncio.run(
            broadcaster.synchronize(
                state_dict={"weight": torch.zeros(2)},
                fetch_from_store=fetch,
                apply_state_dict=apply,
            )
        )

    assert len(collectives.broadcasts) == 1


def test_rejects_unexpected_dp_group_size() -> None:
    with pytest.raises(ValueError, match="DP group size"):
        DPWeightBroadcaster(
            dp_group=_group(world_size=2),
            control_group=_control_group(),
            expected_dp_degree=4,
        )
