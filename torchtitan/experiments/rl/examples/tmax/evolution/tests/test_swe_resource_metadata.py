# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""SWE metadata must reach the allocations consumed by sandbox creation."""

import pandas as pd

from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import _load_resource_map


def test_measured_peaks_replace_template_allocations(tmp_path):
    path = tmp_path / "tasks.parquet"
    pd.DataFrame(
        [
            {
                "repo": "example/repo",
                "instance_id": "rebench-1",
                "peak_ram_mb": 2500,
                "peak_disk_mb": 1800,
                "req_memory_mb": 4096,
                "req_cpus": 1,
            },
            {
                "repo": "example/repo",
                "instance_id": "rebench-2",
                "peak_ram_mb": 400,
                "peak_disk_mb": 300,
                "req_memory_mb": 4096,
                "req_cpus": 2,
            },
        ]
    ).to_parquet(path)
    rows = _load_resource_map(str(path))
    assert rows == {
        "rebench-1": {"daytona_cpu": 1, "daytona_mem_gb": 4, "daytona_disk_gb": 3},
        "rebench-2": {"daytona_cpu": 2, "daytona_mem_gb": 2, "daytona_disk_gb": 2},
    }


def test_missing_measurements_preserve_legacy_allocations(tmp_path):
    path = tmp_path / "tasks.parquet"
    pd.DataFrame(
        [
            {
                "task_id": "smith-1",
                "peak_ram_mb": float("nan"),
                "peak_disk_mb": 0,
                "req_memory_mb": 3072,
                "est_disk_mb": 2048,
                "req_cpus": 1,
            },
            {"task_id": "smith-2", "peak_ram_mb": float("inf"), "peak_disk_mb": -1},
        ]
    ).to_parquet(path)
    assert _load_resource_map(str(path)) == {
        "smith-1": {"daytona_cpu": 1, "daytona_mem_gb": 3, "daytona_disk_gb": 10}
    }
