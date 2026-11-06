# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_EASIER_KEEP_CHECKS: the blind verifier of a simplification keeps every
inherited check whose requirement the instruction still states; off, its
AGENTS.md and every prompt are unchanged."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_codex as ec
from test_blind_verifier import _rewrite, _wire, SEED

# The off-path sentences, spelled out so a change to either is a test failure.
OFF_NOTE = (
    "A requirement this diff removes or narrows loses or narrows its "
    "inherited checks: an inherited check of the old scope now rejects work the "
    "instruction permits. Where an unchanged sentence still reads the old way, "
)
OFF_DEFECTS = (
    "while still checking the required content -- the bound was never the "
    "task's, so this is not widening; if it does not, keep the check."
)


def _verifier_session(tmp_path, monkeypatch, *, simplify: bool) -> dict:
    # Pinned: both default on, and neither may move what this test compares.
    monkeypatch.setenv("EVOLVE_HARDER_QUIET", "0")
    monkeypatch.setenv("EVOLVE_PEER_CONTEXT", "0")
    rw = _rewrite(tmp_path, monkeypatch)
    seed_dir = tmp_path / "seed"
    (seed_dir).mkdir()
    (seed_dir / "instruction.md").write_text(SEED["instruction"])
    (rw.package / "instruction.md").write_text(
        "A starter gendiff is installed; keep it working.\n" + SEED["instruction"]
    )
    (rw.package / "run").mkdir(exist_ok=True)
    (rw.package / "run" / "repair.json").write_text(json.dumps([{
        "defect": "the check rejects a permitted answer", "evidence": "attempt-01.jsonl turn 3",
        "change": "accept it", "kind": "hidden_requirement",
    }]))
    sessions = []
    _wire(monkeypatch, sessions, [])
    task = {**SEED, "_seed_dir": str(seed_dir)}
    if simplify:
        task["_simplify_hint"] = "vague"
    ec._blind_verifier(rw, task, ec.ev.file_map(SEED))
    session = sessions[0]
    return {
        "prompt": " ".join(session["prompt"].split()),
        "agents": (session["cwd"] / "AGENTS.md").read_text(),
        "cwd": session["cwd"],
    }


@pytest.mark.parametrize("simplify", [True, False])
def test_off_leaves_the_verifier_spec_and_prompt_unchanged(tmp_path, monkeypatch, simplify):
    monkeypatch.delenv("EVOLVE_EASIER_KEEP_CHECKS", raising=False)
    seen = _verifier_session(tmp_path, monkeypatch, simplify=simplify)
    assert seen["agents"] == ec.VERIFIER_SPEC.read_text()
    assert OFF_NOTE in seen["prompt"]
    assert OFF_DEFECTS in seen["prompt"]
    assert "Work the revision now supplies" not in seen["prompt"]
    assert ec._REPORTED_KEEP not in seen["prompt"]


def test_on_rewrites_the_three_sentences_for_a_simplification(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_EASIER_KEEP_CHECKS", "1")
    seen = _verifier_session(tmp_path, monkeypatch, simplify=True)
    spec = seen["agents"]
    assert ec._SPEC_FINAL_ARTIFACT not in spec and ec._SPEC_FINAL_ARTIFACT_KEEP in spec
    assert ec._SPEC_METHOD_EVIDENCE not in spec and ec._SPEC_METHOD_EVIDENCE_KEEP in spec
    # Only those two passages change.
    assert spec.replace(ec._SPEC_FINAL_ARTIFACT_KEEP, ec._SPEC_FINAL_ARTIFACT).replace(
        ec._SPEC_METHOD_EVIDENCE_KEEP, ec._SPEC_METHOD_EVIDENCE
    ) == ec.VERIFIER_SPEC.read_text()
    prompt = seen["prompt"]
    assert "removes a requirement only when the revised instruction no longer states it anywhere" in prompt
    assert "Work the revision now supplies" in prompt
    assert "the bound was never the task's" not in prompt
    assert " ".join(ec._REPORTED_KEEP.split()) in prompt
    # A resumed repair reads the same choice from the session's own AGENTS.md.
    assert ec._keeps_checks(seen["cwd"])


def test_on_leaves_a_hardening_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_EASIER_KEEP_CHECKS", "1")
    seen = _verifier_session(tmp_path, monkeypatch, simplify=False)
    assert seen["agents"] == ec.VERIFIER_SPEC.read_text()
    assert OFF_NOTE in seen["prompt"] and OFF_DEFECTS in seen["prompt"]
    assert not ec._keeps_checks(seen["cwd"])
