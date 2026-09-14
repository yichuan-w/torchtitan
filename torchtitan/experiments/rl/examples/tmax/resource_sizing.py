# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""TMax allocations from the current campaign's total peaks, without baseline subtraction."""

from __future__ import annotations

import math

CENSORED_RAM_GIB = 6
HEADROOM = 1.3
MEM_CAP_GIB, DISK_CAP_GIB = 8, 10
MISSING_RESOURCE_GIB = 2


def measured_gib(value, cap: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("a resource peak must be a number or null")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("a resource peak must be finite and positive")
    return min(max(math.ceil(value * HEADROOM / 1024), 1), cap)


def allocation(row: dict) -> dict:
    """The six-GiB censored-RAM value is a policy, never a fabricated measurement.

    Disk remains usable when RAM is censored. Historical env/task-only columns,
    archived measurements, and old agent/oracle peaks never enter this rule.
    """
    censored = row.get("peak_ram_mb_censored", False)
    if not isinstance(censored, bool):
        raise ValueError("peak_ram_mb_censored must be a boolean")
    ram = row["peak_ram_mb"]
    if censored and ram is not None:
        raise ValueError(
            "censored RAM must have a null peak, with its bound recorded separately"
        )
    mem = CENSORED_RAM_GIB if censored else measured_gib(ram, MEM_CAP_GIB)
    disk = measured_gib(row["peak_disk_mb"], DISK_CAP_GIB)
    return {
        "cpu": 1,
        "mem_gb": mem if mem is not None else MISSING_RESOURCE_GIB,
        "disk_gb": disk if disk is not None else MISSING_RESOURCE_GIB,
        "memory_source": (
            "policy:censored_ram_6_gib"
            if censored
            else "measured:latest_peak"
            if mem is not None
            else "policy:missing_peak_2_gib"
        ),
        "disk_source": "measured:latest_peak"
        if disk is not None
        else "policy:missing_peak_2_gib",
        "peak_campaign": row.get("peak_campaign"),
    }


def load_allocations(path: str) -> dict[str, dict]:
    import pyarrow.parquet as pq

    rows = pq.read_table(path).to_pylist()
    out = {}
    for row in rows:
        tid = row["task_id"]
        if not isinstance(tid, str) or not tid or tid in out:
            raise ValueError("peaks must have unique, non-empty task IDs")
        out[tid] = allocation(row)
    return out
