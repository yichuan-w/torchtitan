# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Verifier bootstrap failures must not become student capability failures."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from torchtitan.experiments.rl.examples.tmax import grading


DOWNLOAD_FAILURE = """error: Request failed after 3 retries
  Caused by: Failed to download https://github.com/astral-sh/python-build-standalone/releases/download/example/python.tar.gz
  Caused by: HTTP status server error (504 Gateway Timeout) for url
"""
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
