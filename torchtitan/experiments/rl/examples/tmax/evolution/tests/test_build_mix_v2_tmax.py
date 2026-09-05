"""The TMax side of a seed mix is built from the reaudit task packages -- the
same directory the loop copies r0 from -- with the pin hook read off the
reaudit parquet and the sandbox size off the measured peaks."""
from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_mix_v2 as bm

IMAGE = "hamishi740/swerl-tmax-v3:37a79d0fd9b9"
STAMP = "image:" + IMAGE
HOOK = "set -u\nexit 0\n"


@pytest.fixture(autouse=True)
def _checkout(monkeypatch):
    monkeypatch.setenv("TRL_TT", str(Path(__file__).resolve().parents[7]))


def _package(tasks: Path, tid: str) -> None:
    pkg = tasks / tid
    (pkg / "environment").mkdir(parents=True)
    (pkg / "tests").mkdir()
    (pkg / "solution").mkdir()
    (pkg / "environment/Dockerfile").write_text(f"FROM {IMAGE}\n# setup.sh inlined\n")
    (pkg / "instruction.md").write_text(f"Task {tid} in /home/user.\n")
    (pkg / "tests/test.sh").write_text("echo 1 > /logs/verifier/reward.txt\n")
    (pkg / "solution/solve.sh").write_text("#!/bin/bash\ntouch /home/user/done\n")
    (pkg / "setup.sh").write_text("#!/bin/bash\n")


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    tasks = tmp_path / "tasks"
    for tid in ("task_a", "task_b"):
        _package(tasks, tid)
    reaudit = tmp_path / "reaudit.parquet"
    pq.write_table(pa.table({
        "task_id": ["task_a", "task_b", "task_c"],
        "terminal_domain": ["data-science", "security", "debugging"],
        "pre_test_sh": [HOOK, "", ""],
        "pre_test_env_identity": [STAMP, "", ""],
    }), reaudit)
    peaks = tmp_path / "reaudit_full.parquet"
    pq.write_table(pa.table({
        "task_id": ["task_a", "task_b", "task_c"],
        "peak_ram_mb": [3000.0, 5000.0, None],
        "peak_disk_mb": [300.0, 5000.0, None],
        "ram_at_ceiling": [False, True, None],
        "disk_at_ceiling": [False, False, None],
    }), peaks)
    return tasks, reaudit, peaks


def test_tmax_rows_come_from_the_packages_with_hook_domain_and_size(tmp_path) -> None:
    tasks, reaudit, peaks = _inputs(tmp_path)

    rows, missing = bm.tmax_rows(tasks, reaudit, peaks)

    assert missing == ["task_c"]                      # in the parquet, no package
    by_id = {r["metadata"]["instance_id"]: r for r in rows}
    assert sorted(by_id) == ["task_a", "task_b"]
    a = by_id["task_a"]["metadata"]
    # A dockerfile row, as the loop's fold would build it from the same package.
    assert a["image"] == "" and a["dockerfile"].startswith(f"FROM {IMAGE}")
    assert a["terminal_domain"] == "data-science"
    assert a["tmax"]["pre_test_sh"] == HOOK
    assert a["tmax"]["pretest_env_identity"] == STAMP
    assert a["tmax"]["pretest_episode_env_identity"] == STAMP
    # Measured peaks size the sandbox: 3000 MB * 1.3 -> 4 GiB, 300 MB -> the 1 GiB floor.
    assert (a.get("daytona_cpu"), a["daytona_mem_gb"], a["daytona_disk_gb"]) == (None, 4, 1)
    b = by_id["task_b"]["metadata"]
    assert "pre_test_sh" not in b["tmax"]
    assert b["terminal_domain"] == "security"
    # A reading taken at the ceiling is the cap, not the requirement: left to the fleet default.
    assert not {"daytona_mem_gb", "daytona_disk_gb"} & set(b)


def test_tmax_rows_without_a_peaks_file_size_nothing(tmp_path) -> None:
    tasks, reaudit, _ = _inputs(tmp_path)

    rows, _missing = bm.tmax_rows(tasks, reaudit, None)

    for r in rows:
        assert not {"daytona_mem_gb", "daytona_disk_gb"} & set(r["metadata"])
    assert {r["metadata"]["instance_id"] for r in rows} == {"task_a", "task_b"}


def test_tree_digest_changes_with_any_package_byte(tmp_path) -> None:
    tasks, _reaudit, _peaks = _inputs(tmp_path)
    before = bm._sha_tree(tasks)
    assert before == bm._sha_tree(tasks)
    (tasks / "task_a" / "tests" / "test.sh").write_text("echo 0 > /logs/verifier/reward.txt\n")
    assert bm._sha_tree(tasks) != before
