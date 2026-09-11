# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Dense data-parallel weight synchronization helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import cast, Protocol, TypeVar

import torch
import torch.distributed as dist
from torch.distributed.tensor import DTensor

from torchtitan.observability import structured_logger as sl


logger: logging.Logger = logging.getLogger(__name__)

_BROADCAST_BUCKET_BYTES = 256 * 1024 * 1024

_TensorMetadata = tuple[
    str,
    tuple[int, ...],
    tuple[int, ...],
    str,
    str,
    str,
]
_T = TypeVar("_T")


class _DPGroup(Protocol):
    rank_in_group: int
    world_size: int
    cpu_group: dist.ProcessGroup
    device_group: dist.ProcessGroup


def dp_weight_broadcast_unsupported_reason(
    *, data_parallel_degree: int, expert_parallel_degree: int
) -> str | None:
    if data_parallel_degree <= 1:
        return "data_parallel_degree must be greater than 1"
    if expert_parallel_degree > 1:
        return "expert-parallel weights are not replicated across DP ranks"
    return None


class DPWeightBroadcaster:
    """Fetch one copy of each TP shard, then fan it out across DP replicas."""

    def __init__(
        self,
        *,
        dp_group: _DPGroup,
        control_group: dist.ProcessGroup,
        expected_dp_degree: int,
        bucket_bytes: int = _BROADCAST_BUCKET_BYTES,
    ) -> None:
        if dp_group.world_size != expected_dp_degree:
            raise ValueError(
                "vLLM DP group size does not match generator configuration: "
                f"group={dp_group.world_size}, config={expected_dp_degree}"
            )
        self._dp_group = dp_group
        self._control_group = control_group
        self._bucket_bytes = bucket_bytes
        self._validated_metadata: tuple[_TensorMetadata, ...] | None = None
        self._failure_flag = torch.zeros(1, dtype=torch.int32, device="cpu")
        self._no_failure_flag = torch.zeros(1, dtype=torch.int32, device="cpu")

    @property
    def is_store_reader(self) -> bool:
        return self._dp_group.rank_in_group == 0

    async def synchronize(
        self,
        *,
        state_dict: Mapping[str, object],
        fetch_from_store: Callable[[], Awaitable[None]],
        apply_state_dict: Callable[[], None],
    ) -> None:
        await self._validate_payload(state_dict)

        fetch_error: Exception | None = None
        if self.is_store_reader:
            try:
                await fetch_from_store()
            except Exception as error:
                fetch_error = error
        await self._raise_if_any_rank_failed("store fetch", fetch_error)

        post_fetch_error: Exception | None = None
        try:
            if self._payload_metadata(state_dict) != self._validated_metadata:
                raise RuntimeError(
                    "state-dict tensor metadata changed during the store fetch"
                )
        except Exception as error:
            post_fetch_error = error
        await self._raise_if_any_rank_failed(
            "post-fetch payload validation", post_fetch_error
        )

        broadcast_error: Exception | None = None
        try:
            with sl.log_trace_span("pull_model_state_dict_dp_broadcast"):
                self._broadcast_payload(state_dict)
        except Exception as error:
            broadcast_error = error
        await self._raise_if_any_rank_failed("DP broadcast", broadcast_error)

        apply_error: Exception | None = None
        try:
            apply_state_dict()
        except Exception as error:
            apply_error = error
        await self._raise_if_any_rank_failed("state-dict apply", apply_error)

    async def _validate_payload(self, state_dict: Mapping[str, object]) -> None:
        metadata: tuple[_TensorMetadata, ...] = ()
        first_validation = self._validated_metadata is None
        metadata_error: Exception | None = None
        try:
            metadata = self._payload_metadata(state_dict)
        except Exception as error:
            metadata_error = error
        await self._raise_if_any_rank_failed("payload metadata", metadata_error)

        validation_error: Exception | None = None
        try:
            if first_validation:
                peer_metadata = await self._all_gather_object(
                    metadata, self._dp_group.cpu_group
                )
                if any(peer != metadata for peer in peer_metadata):
                    raise RuntimeError(
                        "state-dict tensor metadata differs across DP replicas"
                    )
            elif metadata != self._validated_metadata:
                raise RuntimeError("state-dict tensor metadata changed between pulls")
        except Exception as error:
            validation_error = error

        await self._raise_if_any_rank_failed("payload validation", validation_error)
        if not first_validation:
            return

        self._validated_metadata = metadata
        await self._log_transfer_shape(state_dict)

    async def _log_transfer_shape(self, state_dict: Mapping[str, object]) -> None:
        local_bytes = sum(
            tensor.numel() * tensor.element_size()
            for _, tensor in self._tensor_entries(state_dict)
        )
        rank_info = await self._all_gather_object(
            (
                dist.get_rank(),
                self.is_store_reader,
                local_bytes,
                self._dp_group.world_size,
            ),
            self._control_group,
        )
        if dist.get_rank() != 0:
            return

        legacy_external_bytes = sum(info[2] for info in rank_info)
        source_info = [info for info in rank_info if info[1]]
        optimized_external_bytes = sum(info[2] for info in source_info)
        broadcast_bytes = sum(info[2] * (info[3] - 1) for info in source_info)
        reduction_factor = (
            legacy_external_bytes / optimized_external_bytes
            if optimized_external_bytes
            else 1.0
        )
        logger.info(
            "DP weight broadcast enabled: store_readers=%d, "
            "legacy_external_bytes=%d, optimized_external_bytes=%d, "
            "reduction_factor=%.2f",
            len(source_info),
            legacy_external_bytes,
            optimized_external_bytes,
            reduction_factor,
        )
        sl.log_trace_scalar(
            {
                "weight_sync/dp_broadcast/store_reader_ranks": len(source_info),
                "weight_sync/dp_broadcast/legacy_external_bytes": legacy_external_bytes,
                "weight_sync/dp_broadcast/optimized_external_bytes": optimized_external_bytes,
                "weight_sync/dp_broadcast/broadcast_bytes": broadcast_bytes,
                "weight_sync/dp_broadcast/external_reduction_factor": reduction_factor,
            }
        )

    def _broadcast_payload(self, state_dict: Mapping[str, object]) -> None:
        cpu_tensors: list[torch.Tensor] = []
        cuda_tensors: list[torch.Tensor] = []
        for _, tensor in self._tensor_entries(state_dict):
            if tensor.device.type == "cuda":
                cuda_tensors.append(tensor)
            else:
                cpu_tensors.append(tensor)

        if cuda_tensors:
            dist._broadcast_coalesced(
                self._dp_group.device_group,
                cuda_tensors,
                self._bucket_bytes,
                0,
            )
        if cpu_tensors:
            dist._broadcast_coalesced(
                self._dp_group.cpu_group,
                cpu_tensors,
                self._bucket_bytes,
                0,
            )

    def _payload_metadata(
        self, state_dict: Mapping[str, object]
    ) -> tuple[_TensorMetadata, ...]:
        entries = self._tensor_entries(state_dict)
        if not entries:
            raise ValueError("state dict contains no tensors to synchronize")

        metadata: list[_TensorMetadata] = []
        for name, tensor in entries:
            if tensor.layout != torch.strided:
                raise TypeError(
                    f"DP weight broadcast requires strided tensors: {name} "
                    f"has layout {tensor.layout}"
                )
            if tensor.device.type not in ("cpu", "cuda"):
                raise TypeError(
                    f"DP weight broadcast does not support {tensor.device.type} "
                    f"tensor {name}"
                )
            metadata.append(
                (
                    name,
                    tuple(tensor.shape),
                    tuple(tensor.stride()),
                    str(tensor.dtype),
                    tensor.device.type,
                    str(tensor.layout),
                )
            )
        return tuple(metadata)

    @staticmethod
    def _tensor_entries(
        state_dict: Mapping[str, object],
    ) -> list[tuple[str, torch.Tensor]]:
        entries: list[tuple[str, torch.Tensor]] = []
        for name in sorted(state_dict):
            value = state_dict[name]
            if isinstance(value, DTensor):
                tensor = value.to_local()
            elif isinstance(value, torch.Tensor):
                tensor = value
            else:
                continue
            entries.append((name, tensor.detach()))
        return entries

    async def _raise_if_any_rank_failed(
        self, stage: str, local_error: Exception | None
    ) -> None:
        self._failure_flag.fill_(int(local_error is not None))
        await asyncio.to_thread(
            dist.all_reduce,
            self._failure_flag,
            op=dist.ReduceOp.MAX,
            group=self._control_group,
            async_op=False,
        )
        if torch.equal(self._failure_flag, self._no_failure_flag):
            return

        error_record = (
            dist.get_rank(),
            None
            if local_error is None
            else f"{type(local_error).__name__}: {local_error}",
        )
        records = await self._all_gather_object(error_record, self._control_group)
        failures = [record for record in records if record[1] is not None]
        details = "; ".join(f"rank {rank}: {error}" for rank, error in failures)
        coordinated_error = RuntimeError(
            f"DP weight synchronization failed during {stage}: {details}"
        )
        if local_error is not None:
            raise coordinated_error from local_error
        raise coordinated_error

    @staticmethod
    async def _all_gather_object(value: _T, group: dist.ProcessGroup) -> list[_T]:
        gathered: list[object | None] = [None] * dist.get_world_size(group)
        await asyncio.to_thread(
            dist.all_gather_object,
            gathered,
            value,
            group=group,
        )
        return [cast(_T, item) for item in gathered]
