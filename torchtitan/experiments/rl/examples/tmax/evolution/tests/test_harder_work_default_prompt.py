# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""With EVOLVE_HARDER_WORK and EVOLVE_HARDER_ESCALATE unset, the harder author's
prompt is byte-identical to the one 341e853a (the base of harder_work.py)
wrote. The golden files under golden_harder_work_default/ were captured by
running this test at 341e853a with HARDER_WORK_WRITE_GOLDEN=<dir>; it imports
nothing from harder_work so it runs there unchanged."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_codex as ec
import test_blind_verifier as tb

GOLDEN = Path(__file__).resolve().parent / "golden_harder_work_default"
FLAGS = (
    "EVOLVE_HARDER_WORK",
    "EVOLVE_HARDER_ESCALATE",
    "EVOLVE_HARDER_TARGET",
    "EVOLVE_EASIER_TARGET",
    "EVOLVE_REWRITE_CONTEXT",
    "EVOLVE_PEER_CONTEXT",
    "EVOLVE_HARDER_SILENT",
)


def _solved_attempts(rw, n=12) -> None:
    """Records shaped like training rollouts: a header and turn lines."""
    rw.traces.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        turns = 10 + i
        lines = [json.dumps({"reward": 1.0, "finish_reason": "submit", "turns": turns,
                             "secs": 100.0 * i, "budget_sec": 2400})]
        for t in range(1, turns + 1):
            lines.append(json.dumps({"turn": t, "think": "x" * 50, "analysis": "a",
                                     "plan": "p", "keystrokes": ["ls\n"], "output": "o" * 200}))
        (rw.traces / f"attempt-{i:02d}.jsonl").write_text("\n".join(lines) + "\n")


def _missed_history(rw) -> None:
    """A previous harder rewrite whose next group was 12 of 12."""
    h = rw.traces / "history"
    h.mkdir(parents=True, exist_ok=True)
    (h / "index.jsonl").write_text(json.dumps({
        "job": "harder", "status": "accepted", "input_rev": 0, "result_rev": 1,
        "rewrite": "20261103-000000Z--harder",
        "measured_next": {"rev": 1, "solved": 12, "total": 12, "current_signal": True},
    }) + "\n")
    (h / "summary.md").write_text("one earlier harder rewrite\n")


SCENARIOS = {
    "blind_plain": (True, False),
    "same_plain": (False, False),
    "blind_history": (True, True),
    "same_history": (False, True),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_default_harder_prompt_is_unchanged(tmp_path, monkeypatch, name) -> None:
    for flag in FLAGS:
        monkeypatch.delenv(flag, raising=False)
    blind, history = SCENARIOS[name]
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions: list = []
    tb._wire(monkeypatch, sessions, [])
    if not blind:
        monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "same")
    _solved_attempts(rw)
    if history:
        _missed_history(rw)
    ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    prompt = sessions[0]["prompt"].replace(str(tmp_path), "<TMP>")
    # The session budget line names a wall-clock time.
    prompt = re.sub(r"ended at [^\n]*?\(\d+ minutes? from now\)", "ended at <CLOCK>", prompt)
    out = os.environ.get("HARDER_WORK_WRITE_GOLDEN")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / f"{name}.txt").write_text(prompt)
        return
    assert prompt == (GOLDEN / f"{name}.txt").read_text()
    assert not (rw.package / "run" / "parent_work.json").exists()
