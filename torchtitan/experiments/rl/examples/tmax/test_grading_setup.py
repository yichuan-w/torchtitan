# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Verifier bootstrap failures must not become student capability failures."""

import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from torchtitan.experiments.rl.examples.tmax import grading


DOWNLOAD_FAILURE = (
    "error: Request failed after 3 retries\n"
    "  Caused by: Failed to download https://github.com/"
    "astral-sh/python-build-standalone/releases/download/example/python.tar.gz\n"
    "  Caused by: HTTP status server error (504 Gateway Timeout) for url\n"
)
SCRIPT = (
    "uvx --with pytest pytest /tests/test_state.py\necho 0 > /logs/verifier/reward.txt"
)


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("reward_text", ["0", "nonce"])
def test_bootstrap_failure_raises_without_a_test_verdict(
    monkeypatch, sync, reward_text
):
    monkeypatch.setattr(grading, "_make_nonce", lambda: "nonce")
    if sync:
        sandbox = Mock()
        sandbox.process.exec.side_effect = [
            SimpleNamespace(exit_code=0, result=""),
            SimpleNamespace(exit_code=0, result=""),
            SimpleNamespace(exit_code=0, result="nonce"),
            SimpleNamespace(exit_code=0, result=DOWNLOAD_FAILURE),
            SimpleNamespace(exit_code=0, result=reward_text),
        ]
        with pytest.raises(RuntimeError, match="Python download failed"):
            grading.grade_tmax_daytona(sandbox, {"test_sh": SCRIPT}, workdir="/app")
    else:
        sandbox = AsyncMock()
        sandbox.exec.return_value = (0, "", DOWNLOAD_FAILURE)
        sandbox.read_file.side_effect = ["nonce", reward_text]
        diagnostics = {}
        with pytest.raises(RuntimeError, match="Python download failed"):
            asyncio.run(
                grading.grade_tmax(
                    sandbox,
                    {"test_sh": SCRIPT},
                    workdir="/app",
                    diagnostics=diagnostics,
                )
            )
        assert diagnostics["output_tail"] == DOWNLOAD_FAILURE


@pytest.mark.parametrize(
    "output,script,reward",
    [
        (DOWNLOAD_FAILURE, SCRIPT, 1.0),
        ("test session starts\n" + DOWNLOAD_FAILURE, SCRIPT, 0.0),
        ("short test summary info\n" + DOWNLOAD_FAILURE, SCRIPT, 0.0),
        (DOWNLOAD_FAILURE, "python /app/program.py", 0.0),
        (DOWNLOAD_FAILURE.replace("504 Gateway Timeout", "404 Not Found"), SCRIPT, 0.0),
        ("AssertionError: incorrect result", SCRIPT, 0.0),
    ],
)
def test_other_grades_are_not_voided(output, script, reward):
    grading._check_verifier_python_download(script, output, reward)


@pytest.mark.parametrize("pre_grade_rc,raises", [(124, True), (0, False)])
def test_pre_grade_timeout_is_unscored_but_an_unwritable_reward_path_scores_0(
    monkeypatch, pre_grade_rc, raises
):
    monkeypatch.setattr(grading, "_make_nonce", lambda: "nonce")
    sandbox = AsyncMock()
    # mkdir, then the pre-grade setup; the sentinel never lands.
    sandbox.exec.side_effect = [(0, "", ""), (pre_grade_rc, "", "")]
    sandbox.read_file.return_value = ""
    diagnostics = {}
    grade = grading.grade_tmax(
        sandbox, {"test_sh": SCRIPT}, workdir="/app", diagnostics=diagnostics
    )
    if raises:
        with pytest.raises(RuntimeError, match="pre-grade setup timed out"):
            asyncio.run(grade)
    else:
        assert asyncio.run(grade) == 0.0
        assert diagnostics == {"stage": "pre_grade", "verifier_ran": False}


def _first_exec(upload_tests: bool) -> str:
    sandbox = AsyncMock()
    # Stop right after the setup command: the sentinel never lands.
    sandbox.exec.side_effect = [(0, "", ""), (0, "", "")]
    sandbox.read_file.return_value = ""
    asyncio.run(
        grading.grade_tmax(
            sandbox, {"test_sh": SCRIPT}, workdir="/app", upload_tests=upload_tests
        )
    )
    return sandbox.exec.call_args_list[0].args[0]


def test_tests_dir_is_emptied_before_upload(monkeypatch, tmp_path):
    monkeypatch.delenv("TMAX_CLEAN_TESTS_DIR", raising=False)
    setup = _first_exec(upload_tests=True)
    assert setup.index("rm -rf /tests;") < setup.index("mkdir -p")
    # A separate verifier image's own /tests is never touched.
    assert "rm -rf" not in _first_exec(upload_tests=False)
    # Run for real against a stand-in /tests holding a planted conftest.py.
    tests = tmp_path / "tests"
    (tests / "sub").mkdir(parents=True)
    (tests / "conftest.py").write_text("raise SystemExit")
    (tests / "sub" / "pytest.ini").write_text("[pytest]")
    monkeypatch.setattr(grading, "_TESTS_DIR", str(tests))
    subprocess.run(
        ["sh", "-c", f"{grading._clear_tests_command()}mkdir -p {tests}"], check=True
    )
    assert tests.is_dir() and not any(tests.iterdir())


def test_tests_dir_cleaning_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("TMAX_CLEAN_TESTS_DIR", "0")
    assert grading._clear_tests_command() == ""
    assert "rm -rf" not in _first_exec(upload_tests=True)
    sandbox = Mock()
    sandbox.process.exec.side_effect = RuntimeError("stop")
    with pytest.raises(RuntimeError):
        grading.grade_tmax_daytona(sandbox, {"test_sh": SCRIPT}, workdir="/app")
    assert "rm -rf" not in sandbox.process.exec.call_args.args[0]


def test_daytona_grade_empties_tests_dir(monkeypatch):
    monkeypatch.delenv("TMAX_CLEAN_TESTS_DIR", raising=False)
    sandbox = Mock()
    sandbox.process.exec.side_effect = RuntimeError("stop")
    with pytest.raises(RuntimeError):
        grading.grade_tmax_daytona(sandbox, {"test_sh": SCRIPT}, workdir="/app")
    assert "rm -rf /tests;" in sandbox.process.exec.call_args.args[0]
