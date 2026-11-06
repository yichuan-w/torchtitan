# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import json
import subprocess
import sys
from pathlib import Path

import pytest

STAGE = Path(__file__).resolve().parent.parent / "offline_stage.py"


def _source_run(base: Path, direction: str = "harder", rev: int = 0) -> Path:
    run = base / "runs" / "tmax-9b--20260911-000700Z"
    (run / "signals").mkdir(parents=True)
    (run / "rollouts" / "task_a").mkdir(parents=True)
    (run / "launch.json").write_text("{}")
    (run / "rollouts" / "task_a" / "g7-r0.jsonl").write_text('{"reward": 1}\n')
    (run / "signals" / "task_a--g7.json").write_text(
        json.dumps(
            {
                "task": "task_a",
                "direction": direction,
                "run": run.name,
                "group": 7,
                "rev": rev,
                "solved": 12 if direction == "harder" else 0,
                "total": 12,
                "attempts": ["rollouts/task_a/g7-r0.jsonl"],
            }
        )
    )
    return run


@pytest.mark.parametrize("direction", ["harder", "easier"])
def test_staged_signal_names_the_staged_copy(tmp_path, direction):
    """The loop resolves a signal's attempts under runs/<signal.run>/, so a
    signal staged under a new name has to point at that copy, not at the run
    it was cut from (which the root need not hold)."""
    run = _source_run(tmp_path / "src", direction)
    root = tmp_path / "root"
    root.mkdir()
    selected = tmp_path / "selected.json"
    selected.write_text(json.dumps({"task_a": {"group": 7}}))
    out = subprocess.run(
        [
            sys.executable,
            str(STAGE),
            "--run",
            str(run),
            "--selected",
            str(selected),
            "--root",
            str(root),
            "--name",
            "staged-1",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out.startswith("staged 1 signals, 1 rollout records")
    staged = root / "runs" / "staged-1"
    signal = json.loads((staged / "signals" / "task_a--g7.json").read_text())
    assert signal["run"] == "staged-1"
    assert signal["attempts"] == ["rollouts/task_a/g7-r0.jsonl"]
    assert (
        staged / "rollouts" / "task_a" / "g7-r0.jsonl"
    ).read_text() == '{"reward": 1}\n'


@pytest.mark.parametrize(
    "signal_rev,revs,staged",
    [(0, [], True), (0, [1], False), (1, [1], True), (1, [1, 2], False)],
)
def test_skip_accepted_compares_with_the_signal_rev(tmp_path, signal_rev, revs, staged):
    """A signal from attempts on r1 asks for a rewrite of r1, so the r1 that
    answered the first signal does not make --skip-accepted drop it; only a
    revision newer than the one the attempts ran on does."""
    run = _source_run(tmp_path / "src", "easier", rev=signal_rev)
    root = tmp_path / "root"
    for r in revs:
        (root / "evolution" / "tasks" / "task_a" / f"r{r}").mkdir(parents=True)
    root.mkdir(exist_ok=True)
    selected = tmp_path / "selected.json"
    selected.write_text(json.dumps({"task_a": {"group": 7}}))
    out = subprocess.run(
        [sys.executable, str(STAGE), "--run", str(run), "--selected", str(selected),
         "--root", str(root), "--name", "staged-1", "--skip-accepted"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out.startswith(f"staged {int(staged)} signals")


@pytest.mark.parametrize(
    "signal_rev,rewrites,staged",
    [
        (0, [], True),
        (0, [(0, "accepted")], False),
        (0, [(0, "failed")], True),
        (0, [(0, "failed"), (0, "failed")], False),
        # A chain hop: the accepted rewrite of r0 answered the previous signal.
        (1, [(0, "accepted")], True),
        (1, [(0, "accepted"), (1, "failed")], True),
        (1, [(0, "accepted"), (1, "accepted")], False),
    ],
)
def test_only_failed_counts_rewrites_of_the_signal_rev(tmp_path, signal_rev, rewrites, staged):
    """--only-failed and --max-attempts read only the rewrites whose input is
    the revision the signal measured, so a chain hop's signal at r1 is not
    taken for already answered by the accepted rewrite of r0."""
    run = _source_run(tmp_path / "src", "easier", rev=signal_rev)
    task = tmp_path / "root" / "evolution" / "tasks" / "task_a"
    for r in range(signal_rev + 1):
        (task / f"r{r}").mkdir(parents=True)
    for i, (input_rev, status) in enumerate(rewrites):
        d = task / "rewrites" / f"20261106-00000{i}Z--easier"
        d.mkdir(parents=True)
        (d / "rewrite.json").write_text(json.dumps({"input_rev": input_rev, "status": status}))
    selected = tmp_path / "selected.json"
    selected.write_text(json.dumps({"task_a": {"group": 7}}))
    out = subprocess.run(
        [sys.executable, str(STAGE), "--run", str(run), "--selected", str(selected),
         "--root", str(tmp_path / "root"), "--name", "staged-1", "--skip-accepted",
         "--only-failed", "--max-attempts", "2", "--attempts-since", "20261106"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out.startswith(f"staged {int(staged)} signals")
