# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import base64
import asyncio
import json
import shutil
import subprocess
import sys
import types
from contextlib import asynccontextmanager

import pytest

from torchtitan.experiments.rl.examples.tmax.prepare_tb2_1_data import (
    _B64_SUFFIX,
    build_rows,
    verify_runtime,
)

_DOCKERFILE = """FROM ubuntu:24.04
WORKDIR /app
WORKDIR /srv/final
COPY protected.tar.gz.enc /protected/protected.tar.gz.enc
"""

_TEST_SH = """#!/bin/bash
tar -xzf /tests/blob.bin -C /tests
mkdir -p /logs/verifier
echo 1 > /logs/verifier/reward.txt
"""

# Shaped like a real TB-2.1 task.toml (schema 1.1): agent and verifier each carry a
# timeout_sec, and [environment] states cpus/memory_mb/storage_mb.
_TASK_TOML = """schema_version = "1.1"

[task]
name = "terminal-bench/demo"

[verifier]
timeout_sec = 1800.0

[agent]
timeout_sec = 3600.0

[environment]
build_timeout_sec = 600.0
docker_image = "alexgshaw/demo:20260403"
cpus = 4
memory_mb = 8192
storage_mb = 10240
gpus = 0
"""

# Not valid UTF-8 -- stands in for reference.jpg / weights_gtruth.pt / *.tar.gz.
_BINARY = b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01binary payload\x00\xff"


def test_runtime_checks_fresh_sandboxes_and_resumes_exact_rows(tmp_path, monkeypatch):
    boots = []

    @asynccontextmanager
    async def boot(image, **kwargs):
        assert kwargs["install_claude"] is False
        boots.append(image)

        async def execute(command, **kwargs):
            assert "apt-get" not in command
            assert "new-session" in command and "kill-server" in command
            return 0, "tmux 3.4", ""

        yield types.SimpleNamespace(sandbox_id=f"box-{len(boots)}", exec=execute)

    monkeypatch.setitem(
        sys.modules,
        "torchtitan.experiments.rl.harness.agents.claude_code",
        types.SimpleNamespace(boot_agent_sandbox=boot),
    )
    row = {
        "metadata": {
            "instance_id": "task",
            "image": "base:v1",
            "dockerfile": "FROM base:v1",
        }
    }
    asyncio.run(verify_runtime([row], str(tmp_path)))
    asyncio.run(verify_runtime([row], str(tmp_path)))
    assert len(boots) == 2
    records = [
        json.loads(line)
        for line in (tmp_path / "progress.jsonl").read_text().splitlines()
    ]
    assert [r["state"] for r in records] == [
        "prepare",
        "start",
        "pass",
        "prepare",
        "skip",
    ]
    row["metadata"]["dockerfile"] += "\nRUN tmux -V"
    asyncio.run(verify_runtime([row], str(tmp_path)))
    assert len(boots) == 4


def test_runtime_probe_failure_never_creates_success_evidence(tmp_path, monkeypatch):
    @asynccontextmanager
    async def boot(*args, **kwargs):
        async def execute(*args, **kwargs):
            return 127, "", "tmux: command not found"

        yield types.SimpleNamespace(sandbox_id="bad-box", exec=execute)

    monkeypatch.setitem(
        sys.modules,
        "torchtitan.experiments.rl.harness.agents.claude_code",
        types.SimpleNamespace(boot_agent_sandbox=boot),
    )
    row = {
        "metadata": {
            "instance_id": "task",
            "image": "base:v1",
            "dockerfile": "FROM base:v1",
        }
    }
    with pytest.raises(RuntimeError, match="tmux probe failed"):
        asyncio.run(verify_runtime([row], str(tmp_path)))
    assert list(tmp_path.glob("*.json")) == []
    assert (
        json.loads((tmp_path / "progress.jsonl").read_text().splitlines()[-1])["state"]
        == "fail"
    )


def _write_task(root, task_id: str, *, task_toml: str = _TASK_TOML, binary=_BINARY):
    """Lay out one TB-2.1-shaped task dir under ``root``."""
    task = root / task_id
    (task / "environment").mkdir(parents=True)
    (task / "tests" / "nested").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text(_DOCKERFILE)
    (task / "instruction.md").write_text(f"do the thing for {task_id}")
    (task / "task.toml").write_text(task_toml)
    (task / "tests" / "test.sh").write_text(_TEST_SH)
    (task / "tests" / "test_outputs.py").write_text("def test_x(): assert True\n")
    (task / "tests" / "nested" / "case.txt").write_text("nested fixture\n")
    if binary is not None:
        (task / "tests" / "blob.bin").write_bytes(binary)
    return task


def test_finds_tasks_under_the_2_1_tasks_subdir(tmp_path):
    """TB-2.1 nests task dirs under ``tasks/``; TB-2.0 had them at the root.

    ``dataset.toml`` sits beside them as a *file*, so name-only probing would trip
    over it. This is the bug that makes prepare_tb2_data.py emit zero rows on a 2.1
    tree.
    """
    repo = tmp_path / "terminal-bench-2-1"
    (repo / "tasks").mkdir(parents=True)
    (repo / "tasks" / "dataset.toml").write_text('[dataset]\nname = "x"\n')
    (repo / "registry.json").write_text("[]")
    _write_task(repo / "tasks", "demo-task")

    rows, skipped = build_rows(tasks_root=str(repo))

    assert skipped == {}
    assert [row["label"] for row in rows] == ["demo-task"]


def test_accepts_a_flat_2_0_style_root(tmp_path):
    root = tmp_path / "flat"
    root.mkdir()
    _write_task(root, "demo-task")

    rows, _ = build_rows(tasks_root=str(root))

    assert [row["label"] for row in rows] == ["demo-task"]


def test_row_carries_image_workdir_and_timeouts(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "demo-task")

    (row,), _ = build_rows(tasks_root=str(root))
    md = row["metadata"]

    assert md["image"] == "docker.io/alexgshaw/demo:20260403"
    # The LAST WORKDIR wins -- that is where the agent's commands land.
    assert md["workdir"] == "/srv/final"
    # [agent] and [verifier] both declare timeout_sec; each must land on its own key.
    assert md["agent_timeout_sec"] == 3600.0
    assert md["verifier_timeout_sec"] == 1800.0
    assert md["tb_version"] == "2.1"


def test_row_carries_per_task_daytona_resources(tmp_path):
    """``[environment] cpus/memory_mb/storage_mb`` -> the daytona_* fields data.py reads."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "demo-task")

    (row,), _ = build_rows(tasks_root=str(root))
    md = row["metadata"]

    assert md["daytona_cpu"] == 4
    assert md["daytona_mem_gb"] == 8
    assert md["daytona_disk_gb"] == 10


def test_undeclared_resources_are_omitted_not_guessed(tmp_path):
    """A field the task does not state must fall back to the TT_DAYTONA_* default."""
    root = tmp_path / "tasks"
    root.mkdir()
    toml = _TASK_TOML.replace("cpus = 4\n", "").replace("memory_mb = 8192\n", "")
    _write_task(root, "demo-task", task_toml=toml)

    (row,), _ = build_rows(tasks_root=str(root))
    md = row["metadata"]

    assert "daytona_cpu" not in md
    assert "daytona_mem_gb" not in md
    assert md["daytona_disk_gb"] == 10


def test_resources_are_clamped_to_the_floors(tmp_path):
    """An RL agent explores far more than the oracle the declaration was sized for."""
    root = tmp_path / "tasks"
    root.mkdir()
    toml = _TASK_TOML.replace("memory_mb = 8192", "memory_mb = 512").replace(
        "storage_mb = 10240", "storage_mb = 1024"
    )
    _write_task(root, "demo-task", task_toml=toml)

    (row,), _ = build_rows(tasks_root=str(root))
    md = row["metadata"]

    assert md["daytona_mem_gb"] == 2
    assert md["daytona_disk_gb"] == 10


def test_binary_fixtures_ride_as_base64_and_test_sh_gains_the_decoder(tmp_path):
    """The 2.0 script dropped non-UTF-8 fixtures, so verifiers that read one scored 0."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "demo-task")

    (row,), _ = build_rows(tasks_root=str(root))
    fixtures = row["metadata"]["tmax"]["fixtures"]

    # test.sh is uploaded separately, never as a grading fixture; nested paths survive.
    assert set(fixtures) == {
        "tests/test_outputs.py",
        "tests/nested/case.txt",
        "tests/blob.bin" + _B64_SUFFIX,
    }
    assert base64.b64decode(fixtures["tests/blob.bin" + _B64_SUFFIX]) == _BINARY
    test_sh = row["metadata"]["tmax"]["test_sh"]
    assert test_sh.startswith("#!/bin/bash\n")  # shebang stays first
    assert "prepare_tb2_1_data" in test_sh
    # The upstream body is untouched, and still runs after the decode.
    assert test_sh.endswith(_TEST_SH[len("#!/bin/bash\n") :])


def test_test_sh_is_verbatim_when_no_binary_fixture(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "demo-task", binary=None)

    (row,), _ = build_rows(tasks_root=str(root))

    assert row["metadata"]["tmax"]["test_sh"] == _TEST_SH


def test_no_binary_fixtures_flag_restores_the_lossy_behaviour(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "demo-task")

    (row,), _ = build_rows(tasks_root=str(root), include_binary=False)

    assert not any(k.endswith(_B64_SUFFIX) for k in row["metadata"]["tmax"]["fixtures"])
    assert row["metadata"]["tmax"]["test_sh"] == _TEST_SH


def test_crlf_fixture_survives_byte_for_byte(tmp_path):
    """Universal-newline translation silently ate 310 bytes of sparql-university's
    university_graph_test.ttl. Graded inputs must reach /tests unchanged."""
    root = tmp_path / "tasks"
    root.mkdir()
    task = _write_task(root, "demo-task", binary=None)
    (task / "tests" / "crlf.ttl").write_bytes(b"a\r\nb\r\nc\r\n")

    (row,), _ = build_rows(tasks_root=str(root))

    assert row["metadata"]["tmax"]["fixtures"]["tests/crlf.ttl"] == "a\r\nb\r\nc\r\n"


def test_unusable_task_is_reported_not_silently_dropped(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    task = _write_task(root, "demo-task")
    (task / "instruction.md").unlink()

    rows, skipped = build_rows(tasks_root=str(root))

    assert rows == []
    assert skipped == {"demo-task": "no instruction.md"}


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_injected_decoder_restores_the_binary_in_a_real_shell(tmp_path):
    """Behavioral check: run the emitted preamble and confirm the bytes come back.

    ``/tests`` is rebound to a temp dir so this stays hermetic; everything else is
    the script that ships in the row.
    """
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "demo-task")
    (row,), _ = build_rows(tasks_root=str(root))

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for rel, content in row["metadata"]["tmax"]["fixtures"].items():
        dest = tests_dir / rel[len("tests/") :]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, newline="")

    test_sh = row["metadata"]["tmax"]["test_sh"]
    start = test_sh.index("# --- injected by prepare_tb2_1_data.py")
    preamble = test_sh[start : test_sh.index("# --- end injected block")]
    proc = subprocess.run(
        ["bash", "-c", preamble.replace("/tests", str(tests_dir))],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert (tests_dir / "blob.bin").read_bytes() == _BINARY
    # The carrier is cleaned up, so the verifier sees the same tree Harbor would.
    assert not (tests_dir / ("blob.bin" + _B64_SUFFIX)).exists()
    assert list(tests_dir.glob("*.tmp")) == []


def test_reads_tb2_0_size_string_resources(tmp_path):
    """TB-2.0 (schema 1.0) states ``memory = "2G"`` / ``storage = "10G"`` instead of
    the 2.1 megabyte fields. A 2.0-style tree must still size from its own
    declaration rather than silently falling back to the TT_DAYTONA_* defaults."""
    root = tmp_path / "flat"
    root.mkdir()
    toml = """version = "1.0"

[verifier]
timeout_sec = 900.0

[agent]
timeout_sec = 900.0

[environment]
docker_image = "alexgshaw/demo:20251031"
cpus = 4
memory = "8G"
storage = "10G"
"""
    _write_task(root, "demo-task", task_toml=toml, binary=None)

    (row,), _ = build_rows(tasks_root=str(root))
    md = row["metadata"]

    assert md["daytona_cpu"] == 4
    assert md["daytona_mem_gb"] == 8
    assert md["daytona_disk_gb"] == 10
    assert md["agent_timeout_sec"] == 900.0


@pytest.mark.parametrize(
    "size,expected",
    [("2G", 2), ("512M", 2), ("8Gi", 8), ("1T", 1024), (6, 6), ("bogus", None)],
)
def test_size_string_parsing(size, expected):
    from torchtitan.experiments.rl.examples.tmax.prepare_tb2_1_data import _size_to_gb

    got = _size_to_gb(None, size)
    # 512M rounds up to 1GiB, then the caller clamps to the 2GiB floor.
    assert got == (1 if size == "512M" else expected)
