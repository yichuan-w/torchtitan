# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import json
import subprocess
import sys
from pathlib import Path

STAGE = Path(__file__).resolve().parent.parent / "offline_stage.py"


def _source_run(base: Path) -> Path:
    run = base / "runs" / "tmax-9b--20260911-000700Z"
    (run / "signals").mkdir(parents=True)
    (run / "rollouts" / "task_a").mkdir(parents=True)
    (run / "launch.json").write_text("{}")
    (run / "rollouts" / "task_a" / "g7-r0.jsonl").write_text('{"reward": 1}\n')
    (run / "signals" / "task_a--g7.json").write_text(
        json.dumps(
            {
                "task": "task_a",
                "direction": "harder",
                "run": run.name,
                "group": 7,
                "rev": 0,
                "solved": 12,
                "total": 12,
                "attempts": ["rollouts/task_a/g7-r0.jsonl"],
            }
        )
    )
    return run


def test_staged_signal_names_the_staged_copy(tmp_path):
    """The loop resolves a signal's attempts under runs/<signal.run>/, so a
    signal staged under a new name has to point at that copy, not at the run
    it was cut from (which the root need not hold)."""
    run = _source_run(tmp_path / "src")
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
