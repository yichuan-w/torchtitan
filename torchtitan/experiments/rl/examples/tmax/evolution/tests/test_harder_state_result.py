# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_HARDER_STATE_RESULT: a harder change states in the instruction the
result it is checked on; off, every prompt is unchanged."""

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
    return " ".join(next(x for x in sessions if x["role"] == "author")["prompt"].split())


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_off_keeps_the_original_sentence(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_HARDER_STATE_RESULT", raising=False)
    monkeypatch.delenv("EVOLVE_HARDER_QUIET", raising=False)
    prompt = _author_prompt(tmp_path, monkeypatch)
    assert _flat(ec._STATE_RESULT[0]) in prompt
    assert _flat(ec._STATE_RESULT[1]) not in prompt


def test_on_replaces_the_sentence(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HARDER_STATE_RESULT", "1")
    prompt = _author_prompt(tmp_path, monkeypatch)
    assert _flat(ec._STATE_RESULT[1]) in prompt
    assert _flat(ec._STATE_RESULT[0]) not in prompt
    # The pitfalls stay in the environment.
    assert "Do not list the pitfalls" in prompt


def test_on_stacks_with_harder_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HARDER_STATE_RESULT", "1")
    monkeypatch.setenv("EVOLVE_HARDER_QUIET", "1")
    prompt = _author_prompt(tmp_path, monkeypatch)
    assert _flat(ec._STATE_RESULT[1]) in prompt
    assert _flat(ec._QUIET_REPLAY[1]) in prompt
