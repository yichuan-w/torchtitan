# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""SWE metadata must reach the allocations consumed by sandbox creation."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_mix_rebench_tmax as mix

from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import _load_resource_map


def test_measured_peaks_use_mix_floors(tmp_path):
    path = tmp_path / "tasks.parquet"
    pd.DataFrame(
        [
            {
                "repo": "example/repo",
                "instance_id": "rebench-1",
                "peak_ram_mb": 2500,
                "peak_disk_mb": 1800,
                "req_cpus": 1,
            },
            {
                "repo": "example/repo",
                "instance_id": "rebench-2",
                "peak_ram_mb": 400,
                "peak_disk_mb": 300,
                "req_cpus": 2,
            },
        ]
    ).to_parquet(path)
    rows = _load_resource_map(str(path))
    assert rows == {
        "rebench-1": {"daytona_cpu": 1, "daytona_mem_gb": 4, "daytona_disk_gb": 3},
        "rebench-2": {"daytona_cpu": 2, "daytona_mem_gb": 1, "daytona_disk_gb": 1},
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
        "smith-1": {"daytona_cpu": 1, "daytona_mem_gb": 3, "daytona_disk_gb": 2}
    }


@pytest.mark.parametrize("kind", ["rebench", "tmax"])
def test_mix_and_generic_export_agree(tmp_path, monkeypatch, kind):
    values = [None, 0, -1, float("nan"), float("inf"), 512, 1024, 2500, 16384]
    records = []
    tasks = tmp_path / "tasks"
    for i, value in enumerate(values):
        tid = f"task-{i}"
        (tasks / tid).mkdir(parents=True)
        (tasks / tid / "instruction.md").write_text("Fixture task")
        record = {
            "instance_id" if kind == "rebench" else "task_id": tid,
            "tb_category": "debugging",
            "peak_ram_mb": value,
            "peak_disk_mb": value,
        }
        if kind == "tmax":
            record.update(req_memory_mb=value, est_disk_mb=value)
            # Deliberately different peaks must not override a valid allocation.
            record.update(peak_ram_mb=7000, peak_disk_mb=7000)
        records.append(record)
    path = tmp_path / "tasks.parquet"
    pd.DataFrame(records).to_parquet(path)
    monkeypatch.setattr(
        mix.pack,
        "to_row",
        lambda path, **kwargs: {"metadata": {"instance_id": Path(path).name}},
    )
    built, missing = (mix.rebench_rows if kind == "rebench" else mix.tmax_rows)(
        tasks, path
    )
    assert missing == []
    imported = _load_resource_map(str(path))
    for row in built:
        md = row["metadata"]
        tid = md["instance_id"]
        assert {
            k: md[k] for k in ("daytona_mem_gb", "daytona_disk_gb") if k in md
        } == imported.get(tid, {})


def test_published_allocations_are_not_inflated_or_overwritten(tmp_path):
    path = tmp_path / "tasks.parquet"
    pd.DataFrame(
        [
            {
                "task_id": "tmax",
                "req_memory_mb": 1024,
                "est_disk_mb": 2048,
                "peak_ram_mb": 4000,
                "peak_disk_mb": 6000,
            }
        ]
    ).to_parquet(path)
    assert _load_resource_map(str(path))["tmax"] == {
        "daytona_mem_gb": 1,
        "daytona_disk_gb": 2,
    }
