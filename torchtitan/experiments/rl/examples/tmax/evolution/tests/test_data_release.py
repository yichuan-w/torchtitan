# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

EVOLUTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVOLUTION))
spec = importlib.util.spec_from_file_location(
    "data_release", EVOLUTION / "data_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def fixture(tmp_path, monkeypatch):
    archive = tmp_path / "tasks.tar"
    with tarfile.open(archive, "w") as bundle:
        for tid in ("task_a", "task_b"):
            for name, content in {
                "instruction.md": "Create /app/result.\n",
                "environment/Dockerfile": "FROM ubuntu:24.04\nWORKDIR /app\n",
                "tests/test.sh": "echo 1 > /logs/verifier/reward.txt\n",
                "solution/solve.sh": "touch /app/result\n",
            }.items():
                member = tarfile.TarInfo(f"tasks/{tid}/{name}")
                member.size = len(content.encode())
                bundle.addfile(member, io.BytesIO(content.encode()))
    metadata = tmp_path / "tasks.parquet"
    pq.write_table(
        pa.table(
            {
                "instance_id": ["task_a", "task_b"],
                "peak_ram_mb": [1500.0, None],
                "peak_disk_mb": [300.0, None],
            }
        ),
        metadata,
    )
    source = {
        "name": "swe",
        "repo": "example/tasks",
        "revision": "a" * 40,
        "adapter": "swe_peaks",
        "metadata": "tasks.parquet",
        "archives": ["tasks.tar"],
        "count": None,
    }
    monkeypatch.setattr(
        release,
        "fetch_source",
        lambda *_: {
            "tasks.parquet": {"path": metadata, "sha256": release.digest(metadata)},
            "tasks.tar": {"path": archive, "sha256": release.digest(archive)},
        },
    )
    monkeypatch.setattr(
        release.subprocess,
        "check_output",
        lambda args, **_: "" if "status" in args else "b" * 40,
    )
    return {"seed": 7, "sources": [source]}


def test_rebuild_resume_and_tampering(tmp_path, monkeypatch):
    config = fixture(tmp_path, monkeypatch)
    first = release.build(config, tmp_path / "first")
    second = release.build(config, tmp_path / "second")
    assert first.name == second.name
    assert release.build(config, tmp_path / "first") == first
    assert (first / "mix.jsonl").read_bytes() == (second / "mix.jsonl").read_bytes()
    rows = {
        row["label"]: row
        for row in map(json.loads, (first / "mix.jsonl").read_text().splitlines())
    }
    assert rows["task_a"]["metadata"]["daytona_mem_gb"] == 2
    assert rows["task_a"]["metadata"]["daytona_disk_gb"] == 1
    assert rows["task_b"]["metadata"]["daytona_mem_gb"] == 2
    assert rows["task_b"]["metadata"]["daytona_disk_gb"] == 2
    assert "tmux" in rows["task_a"]["metadata"]["dockerfile"]
    (second / "mix.jsonl").write_text("corrupt\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        release.verify(second)


def test_published_allocations_are_not_scaled_again():
    result = release.resources(
        {"adapter": "tmax_reaudit"},
        {"req_cpus": 1, "req_memory_mb": 6144, "est_disk_mb": 8192},
    )
    assert result == {"daytona_cpu": 1, "daytona_mem_gb": 6, "daytona_disk_gb": 8}


def test_configuration_rejects_floating_revision(tmp_path, monkeypatch):
    config = fixture(tmp_path, monkeypatch)
    config["sources"][0]["revision"] = "main"
    with pytest.raises(ValueError, match="full commit SHA"):
        release.check_config(config)


def test_source_collision_is_not_silently_deduplicated(tmp_path, monkeypatch):
    config = fixture(tmp_path, monkeypatch)
    config["sources"].append({**config["sources"][0], "name": "other"})
    with pytest.raises(ValueError, match="collision"):
        release.build(config, tmp_path / "output")


def test_selection_is_exact_and_reproducible(tmp_path, monkeypatch):
    config = fixture(tmp_path, monkeypatch)
    config["sources"][0]["count"] = 1
    first = release.build(config, tmp_path / "first")
    second = release.build(config, tmp_path / "second")
    assert first.name == second.name
    assert len((first / "mix.jsonl").read_text().splitlines()) == 1


def test_archive_cannot_escape_destination(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        member = tarfile.TarInfo("../outside")
        member.size = 1
        bundle.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="unsafe archive"):
        release.extract(archive, tmp_path / "unpacked")
    assert not (tmp_path / "outside").exists()
