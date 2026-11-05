# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_HARDER_QUIET: a harder change counts only where its wrong result is
quiet, and the instruction states the user-visible result; off, every prompt
is unchanged."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_codex as ec
from test_blind_verifier import _rewrite, _wire, SEED


def _author_prompt(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("EVOLVE_REWRITE_BUDGET_SEC", "86400")
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    monkeypatch.setattr(ec, "HARDER_GUIDANCE", "real")
    ec.evolve_agentic(rw, dict(SEED), "harder")
    return next(x for x in sessions if x["role"] == "author")["prompt"]


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_off_leaves_the_harder_prompt_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_HARDER_QUIET", raising=False)
    monkeypatch.delenv("EVOLVE_NO_WORKED_ANSWERS", raising=False)
    off = _author_prompt(tmp_path / "off", monkeypatch)
    flat = _flat(off)
    for _old, new in (ec._QUIET_REPLAY, ec._QUIET_REQUIREMENT, ec._QUIET_VERIFIABLE):
        assert _flat(new) not in flat
    assert ec._QUIET_CHECKED_NOTE.strip() not in off
    assert ec._NO_WORKED_ANSWER_NOTE.strip() not in off


def test_on_rewrites_the_three_passages_and_the_checked_note(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_NO_WORKED_ANSWERS", raising=False)
    monkeypatch.setenv("EVOLVE_HARDER_QUIET", "1")
    prompt = _author_prompt(tmp_path, monkeypatch)
    flat = _flat(prompt)
    for old, new in (ec._QUIET_REPLAY, ec._QUIET_REQUIREMENT, ec._QUIET_VERIFIABLE):
        assert _flat(new) in flat
    # The blind job's sentence is replaced, not kept beside the new one.
    assert _flat(ec._QUIET_VERIFIABLE[0]) not in flat
    assert _flat(ec._QUIET_CHECKED_NOTE) in flat
    # EVOLVE_NO_WORKED_ANSWERS comes with it.
    assert _flat(ec._NO_WORKED_ANSWER_NOTE) in flat
    assert ec._verifier_blocked_job() != ec._VERIFIER_BLOCKED_JOB

