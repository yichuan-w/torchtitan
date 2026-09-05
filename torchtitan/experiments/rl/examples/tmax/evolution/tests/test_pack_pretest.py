"""The pin hook rides on a row, not on a package: pack.to_row is handed the
hook the row carries and re-derives this package's environment identity from
its Dockerfile, so a folded row grades exactly as the seed row did (or skips
the check, by design, once the environment moved)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pack_to_dataset as pack

IMAGE = "hamishi740/swerl-tmax-v3:37a79d0fd9b9"
STAMP = "image:" + IMAGE
HOOK = "set -u\nexit 0\n"


@pytest.fixture(autouse=True)
def _checkout(monkeypatch):
    # pack.to_row delegates to the checkout's own prepare_rts_data; this test
    # file sits inside that checkout.
    monkeypatch.setenv("TRL_TT", str(Path(__file__).resolve().parents[7]))


def _package(tmp_path: Path, dockerfile: str = f"FROM {IMAGE}\n# setup.sh, inlined\n") -> Path:
    pkg = tmp_path / "pkg"
    (pkg / "environment").mkdir(parents=True)
    (pkg / "tests").mkdir()
    (pkg / "solution").mkdir()
    (pkg / "environment/Dockerfile").write_text(dockerfile)
    (pkg / "instruction.md").write_text("Do the thing in /home/user.\n")
    (pkg / "tests/test.sh").write_text("echo 1 > /logs/verifier/reward.txt\n")
    (pkg / "solution/solve.sh").write_text("#!/bin/bash\ntouch /home/user/done\n")
    return pkg


def test_row_carries_the_hook_and_this_packages_identity(tmp_path) -> None:
    row = pack.to_row(str(_package(tmp_path)), task_id="task_1", pretest=(HOOK, STAMP))
    tm = row["metadata"]["tmax"]
    assert tm["pre_test_sh"] == HOOK
    assert tm["pretest_env_identity"] == STAMP            # the stamp, verbatim
    assert tm["pretest_episode_env_identity"] == STAMP    # a bare FROM is the image
    assert tm["task_id"] == "task_1"


def test_row_without_a_hook_is_unchanged(tmp_path) -> None:
    pkg = _package(tmp_path)
    plain = pack.to_row(str(pkg), task_id="task_1")
    empty = pack.to_row(str(pkg), task_id="task_1", pretest=("", ""))
    for row in (plain, empty):
        assert not {"pre_test_sh", "pretest_env_identity",
                    "pretest_episode_env_identity", "task_id"} & set(row["metadata"]["tmax"])
    assert plain == empty


def test_a_dockerfile_that_builds_moves_the_identity(tmp_path) -> None:
    pkg = _package(tmp_path, dockerfile=f"FROM {IMAGE}\nRUN echo evolved\n")
    tm = pack.to_row(str(pkg), task_id="task_1", pretest=(HOOK, STAMP))["metadata"]["tmax"]
    assert tm["pretest_env_identity"] == STAMP
    assert tm["pretest_episode_env_identity"].startswith("dockerfile:")
    assert tm["pretest_episode_env_identity"] != STAMP   # grading skips the check
