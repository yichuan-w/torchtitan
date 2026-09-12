# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import base64
import shutil
import subprocess

import pytest

from torchtitan.experiments.rl.examples.tmax.prepare_tbh_data import (
    _B64_SUFFIX,
    build_rows,
)

_TOML = """schema_version = "1.1"

[task]
name = "terminal-bench-hard/tbh_task_deadbeef"

[agent]
timeout_sec = 600.0

[verifier]
timeout_sec = 120.0

[environment]
cpus = 1
memory_mb = 2048
allow_internet = true
"""

_DOCKERFILE = """FROM ubuntu:22.04
COPY base_install.sh /tmp/base_install.sh
RUN bash /tmp/base_install.sh
COPY _fixtures/app/fixtures/data.bin /app/fixtures/data.bin
"""

_TEST_SH = """#!/bin/bash
set -e
mkdir -p /logs/verifier
cd /tests && python3 -m pytest test_final_state.py -v
echo 1 > /logs/verifier/reward.txt
"""

_BINARY = b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01payload\x00\xff"


def _write_task(root, task_id="tbh_task_deadbeef", *, binary=None):
    task = root / task_id
    (task / "environment" / "_fixtures" / "app" / "fixtures").mkdir(parents=True)
    (task / "tests").mkdir(parents=True)
    (task / "task.toml").write_text(_TOML)
    (task / "instruction.md").write_text("do the hard thing")
    (task / "environment" / "Dockerfile").write_text(_DOCKERFILE)
    (task / "environment" / "base_install.sh").write_text("apt-get update\n")
    (task / "environment" / "_fixtures" / "app" / "fixtures" / "data.bin").write_bytes(
        b"\x00\x01\x02not utf8\xff"
    )
    (task / "tests" / "test.sh").write_text(_TEST_SH)
    (task / "tests" / "test_final_state.py").write_text("def test_x(): assert True\n")
    if binary is not None:
        (task / "tests" / "ref.png").write_bytes(binary)
    return task


def test_row_ships_dockerfile_and_build_context_not_an_image(tmp_path):
    """TBH publishes no image; TMaxSample takes image OR dockerfile."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root)

    (row,), skipped = build_rows(tasks_root=str(root))
    md = row["metadata"]

    assert skipped == {}
    assert md["image"] == ""
    assert md["dockerfile"] == _DOCKERFILE
    # Every local COPY source ships, base64 so binaries survive.
    assert set(md["build_context"]) == {
        "base_install.sh",
        "_fixtures/app/fixtures/data.bin",
    }
    assert (
        base64.b64decode(md["build_context"]["_fixtures/app/fixtures/data.bin"])
        == b"\x00\x01\x02not utf8\xff"
    )


def test_accepts_repo_root_or_tasks_dir(tmp_path):
    repo = tmp_path / "Terminal-Bench-Hard"
    (repo / "tasks").mkdir(parents=True)
    (repo / "metadata").mkdir()
    _write_task(repo / "tasks")

    rows_root, _ = build_rows(tasks_root=str(repo))
    rows_tasks, _ = build_rows(tasks_root=str(repo / "tasks"))

    assert [r["label"] for r in rows_root] == ["tbh_task_deadbeef"]
    assert [r["label"] for r in rows_tasks] == ["tbh_task_deadbeef"]


def test_provenance_and_timeouts(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root)

    (row,), _ = build_rows(tasks_root=str(root))
    md = row["metadata"]

    assert md["tb_version"] == "hard"
    assert md["agent_timeout_sec"] == 600.0
    assert md["verifier_timeout_sec"] == 120.0
    assert md["workdir"] == "/app"


def test_storage_is_not_invented(tmp_path):
    """TBH states cpus/memory_mb but never storage_mb; disk must fall back to the
    TT_DAYTONA_DISK_GB default rather than to a guess."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root)

    (row,), _ = build_rows(tasks_root=str(root))
    md = row["metadata"]

    assert md["daytona_cpu"] == 1
    assert md["daytona_mem_gb"] == 2
    assert "daytona_disk_gb" not in md


def test_binary_grading_fixture_rides_as_base64(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, binary=_BINARY)

    (row,), _ = build_rows(tasks_root=str(root))
    fixtures = row["metadata"]["tmax"]["fixtures"]

    # test.sh is uploaded separately by grading.py, never as a fixture.
    assert set(fixtures) == {
        "tests/test_final_state.py",
        "tests/ref.png" + _B64_SUFFIX,
    }
    assert base64.b64decode(fixtures["tests/ref.png" + _B64_SUFFIX]) == _BINARY
    assert "prepare_tbh_data" in row["metadata"]["tmax"]["test_sh"]


def test_test_sh_is_verbatim_without_a_binary_fixture(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root)

    (row,), _ = build_rows(tasks_root=str(root))

    assert row["metadata"]["tmax"]["test_sh"] == _TEST_SH


@pytest.mark.parametrize(
    "missing,reason",
    [
        ("instruction.md", "no instruction.md"),
        ("tests/test.sh", "no tests/test.sh"),
        ("environment/Dockerfile", "no environment/Dockerfile"),
    ],
)
def test_unusable_task_is_reported_not_dropped(tmp_path, missing, reason):
    root = tmp_path / "tasks"
    root.mkdir()
    task = _write_task(root)
    (task / missing).unlink()

    rows, skipped = build_rows(tasks_root=str(root))

    assert rows == []
    assert skipped == {"tbh_task_deadbeef": reason}


def test_missing_copy_source_is_reported(tmp_path):
    """An unbuildable task must be named, not silently dropped."""
    root = tmp_path / "tasks"
    root.mkdir()
    task = _write_task(root)
    (task / "environment" / "base_install.sh").unlink()

    rows, skipped = build_rows(tasks_root=str(root))

    assert rows == []
    assert "build context missing" in skipped["tbh_task_deadbeef"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_injected_decoder_restores_the_binary_in_a_real_shell(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, binary=_BINARY)
    (row,), _ = build_rows(tasks_root=str(root))

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for rel, content in row["metadata"]["tmax"]["fixtures"].items():
        (tests_dir / rel[len("tests/") :]).write_text(content, newline="")

    sh = row["metadata"]["tmax"]["test_sh"]
    preamble = sh[
        sh.index("# --- injected by prepare_tbh_data.py") : sh.index(
            "# --- end injected block"
        )
    ]
    proc = subprocess.run(
        ["bash", "-c", preamble.replace("/tests", str(tests_dir))],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert (tests_dir / "ref.png").read_bytes() == _BINARY
    assert not (tests_dir / ("ref.png" + _B64_SUFFIX)).exists()
