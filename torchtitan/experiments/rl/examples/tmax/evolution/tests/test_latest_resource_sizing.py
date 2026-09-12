# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Current TMax peaks must survive every sizing stage without older inputs taking over."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

EVOLUTION = Path(__file__).resolve().parents[1]
CHECKOUT = EVOLUTION.parents[5]
spec = importlib.util.spec_from_file_location(
    "latest_sizing", EVOLUTION.parent / "resource_sizing.py"
)
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


def test_latest_totals_ignore_obsolete_fields():
    row = {
        "peak_ram_mb": 200.0,
        "peak_disk_mb": 300.0,
        "peak_ram_mb_censored": False,
        "peak_ram_task_mb": 99999.0,
        "disk_env_mb": 99999.0,
        "ram_at_ceiling": True,
        "peak_disk_mb_campaign_20260902": 99999.0,
    }
    result = rs.allocation(row)
    assert (result["mem_gb"], result["disk_gb"]) == (1, 1)


def test_censored_ram_gets_six_without_discarding_disk():
    result = rs.allocation(
        {"peak_ram_mb": None, "peak_disk_mb": 2743.0, "peak_ram_mb_censored": True}
    )
    assert (result["mem_gb"], result["disk_gb"]) == (6, 4)
    assert result["memory_source"] == "policy:censored_ram_6_gib"
    assert result["disk_source"] == "measured:latest_peak"
    with pytest.raises(ValueError, match="null"):
        rs.allocation(
            {"peak_ram_mb": 6144.0, "peak_disk_mb": 300.0, "peak_ram_mb_censored": True}
        )


def _peaks(path):
    pq.write_table(
        pa.table(
            {
                "task_id": ["task_latest", "task_censored"],
                "peak_ram_mb": [200.0, None],
                "peak_disk_mb": [300.0, 2743.0],
                "peak_ram_mb_censored": [False, True],
                "peak_campaign": ["current", "current"],
            }
        ),
        path,
    )


def test_derivation_uses_latest_even_when_old_measurements_are_larger(tmp_path):
    peaks = tmp_path / "peaks.parquet"
    _peaks(peaks)
    agent = tmp_path / "agent.jsonl"
    agent.write_text(
        json.dumps(
            {
                "task_id": "task_latest",
                "peak_ram_mb": 9999,
                "peak_disk_mb": 9999,
                "peak_cpu_cores": 4,
            }
        )
        + "\n"
    )
    oracle = tmp_path / "oracle.jsonl"
    oracle.write_text(
        json.dumps(
            {
                "task_id": "task_latest",
                "mem_peak_mb": 9999,
                "df_used_mb": 9999,
                "cpu_seconds": 3600,
                "reward": 1,
            }
        )
        + "\n"
    )
    decl = tmp_path / "decl.parquet"
    pq.write_table(
        pa.table(
            {"task_id": ["task_latest"], "req_memory_mb": [8192.0], "req_cpus": [4.0]}
        ),
        decl,
    )
    out = tmp_path / "sizing.jsonl"
    subprocess.run(
        [
            sys.executable,
            str(EVOLUTION / "derive_sizing.py"),
            "--agent",
            str(agent),
            "--oracle",
            str(oracle),
            "--decl",
            str(decl),
            "--peer",
            str(peaks),
            "--out",
            str(out),
        ],
        env={**os.environ, "TRL_TT": str(CHECKOUT)},
        check=True,
        capture_output=True,
    )
    rows = {r["task_id"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert set(rows) == {"task_latest", "task_censored"}
    assert (rows["task_latest"]["mem_gb"], rows["task_latest"]["disk_gb"]) == (1, 1)
    assert (rows["task_censored"]["mem_gb"], rows["task_censored"]["disk_gb"]) == (6, 4)


def test_final_apply_cannot_restore_stale_sizes(tmp_path):
    peaks = tmp_path / "peaks.parquet"
    _peaks(peaks)
    mix = tmp_path / "seed.jsonl"
    mix.write_text(
        json.dumps(
            {
                "metadata": {
                    "instance_id": "task_censored",
                    "rev": 0,
                    "daytona_cpu": 1,
                    "daytona_mem_gb": 2,
                    "daytona_disk_gb": 2,
                }
            }
        )
        + "\n"
    )
    old = tmp_path / "old-sizing.jsonl"
    old.write_text(
        json.dumps({"task_id": "task_censored", "cpu": 1, "mem_gb": 2, "disk_gb": 2})
        + "\n"
    )
    command = [
        sys.executable,
        str(EVOLUTION / "apply_audit_sizing.py"),
        "--mix",
        str(mix),
        "--sizing",
        str(old),
        "--include-holdout",
        "--apply",
    ]
    env = {**os.environ, "TRL_TT": str(CHECKOUT)}
    subprocess.run(
        command + ["--tmax-peaks", str(peaks)], env=env, check=True, capture_output=True
    )
    before = mix.read_bytes()
    md = json.loads(before)["metadata"]
    assert (md["daytona_mem_gb"], md["daytona_disk_gb"]) == (6, 4)
    # Even if a later caller forgets --tmax-peaks, the recorded latest allocation
    # must win over the obsolete sizing file.
    subprocess.run(command, env=env, check=True, capture_output=True)
    assert mix.read_bytes() == before
