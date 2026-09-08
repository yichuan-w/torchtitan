# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Measurement reports distinguish endpoint movement from a complete interval."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure_difficulty as md


def _result(solved, graded):
    # solve_daytona excludes reward=None from graded, but preserves attempts.
    return {
        "solved": solved,
        "graded": graded,
        "pass_at_k": solved / graded if graded else None,
        "attempts": (
            [{"reward": 1.0}] * solved
            + [{"reward": 0.0}] * (graded - solved)
            + [{"reward": None, "why": "install failed"}] * (8 - graded)
        ),
        "status": "solved" if solved else "unsolved" if graded else "ungraded",
    }


@pytest.mark.parametrize("job", ["easier", "harder"])
@pytest.mark.parametrize(
    "result,outcome,target",
    [
        (_result(0, 8), "none_solved", False),
        (_result(1, 8), "partial_solved", True),
        (_result(8, 8), "all_solved", False),
        (_result(0, 0), "incomplete", None),
        (_result(0, 7), "incomplete", None),
        (_result(1, 7), "incomplete", None),
        (_result(7, 7), "incomplete", None),
        ({"status": "no_pool_dir"}, "incomplete", None),
        ({"status": "row_error", "why": "invalid package"}, "incomplete", None),
        ({"graded": 8}, "incomplete", None),
        ({"solved": 1}, "incomplete", None),
    ],
)
def test_measurement_output(
    tmp_path, monkeypatch, capsys, job, result, outcome, target
):
    row = {
        "task": "test",
        "rewrite": "rewrite",
        "rev": 1,
        "job": job,
        "before_solved": 0 if job == "easier" else 8,
        "before_total": 8,
        "package": str(tmp_path),
    }
    monkeypatch.setattr(md, "accepted_rewrites", lambda *_: [row])

    async def solve_task(tid, attempts, max_turns, sem, *, src):
        assert tid == "test" and attempts == 8 and src == tmp_path
        return result

    monkeypatch.setattr(md.sd, "solve_task", solve_task)
    args = argparse.Namespace(limit=0, attempts=8, max_turns=25, concurrency=1)
    asyncio.run(md.run(None, args, tmp_path))
    record = json.loads((tmp_path / "results.jsonl").read_text())
    assert record["outcome"] == outcome
    assert record["in_target_interval"] is target
    assert record["planned_attempts"] == 8
    assert record["after_solved"] == result.get("solved")
    assert record["after_graded"] == result.get("graded")
    assert record["attempts"] == result.get("attempts")
    legacy = None
    if result.get("graded") and result.get("solved") is not None:
        legacy = (
            result["solved"] > 0
            if job == "easier"
            else result["solved"] < result["graded"]
        )
    assert record["moved_toward_target"] is legacy
    output = capsys.readouterr().out
    assert "legacy endpoint movement (graded subsets included)" in output
    assert "do not establish validity or stable improvement" in output
    assert (
        f"in target interval (partial_solved): {int(target is True)}/{int(target is not None)}"
        in output
    )
    assert f"incomplete: {int(target is None)}" in output
