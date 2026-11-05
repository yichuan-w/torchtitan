# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_NO_WORKED_ANSWERS: the instruction states rules, never the result for
an input the task itself uses; off, every prompt is unchanged."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_codex as ec
from test_blind_verifier import _rewrite, _wire, SEED


def _author_prompt(tmp_path, monkeypatch) -> str:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    ec.evolve_agentic(rw, dict(SEED), "harder")
    return next(x for x in sessions if x["role"] == "author")["prompt"]


def test_off_leaves_the_author_prompt_and_the_blocked_job_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLVE_NO_WORKED_ANSWERS", raising=False)
    prompt = _author_prompt(tmp_path, monkeypatch)
    assert "WHAT YOUR SCRATCH TESTS CHECK" in prompt
    assert ec._NO_WORKED_ANSWER_NOTE.strip() not in prompt
    assert ec._verifier_blocked_job() == ec._VERIFIER_BLOCKED_JOB


def test_on_adds_the_rule_to_the_author_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_NO_WORKED_ANSWERS", "1")
    prompt = " ".join(_author_prompt(tmp_path, monkeypatch).split())
    assert "It never gives the result for an input the task itself uses" in prompt
    assert "withdraw it rather than keep it or update its numbers" in prompt
    # The rule sits after the checked-behaviours note it qualifies.
    assert prompt.index("WHAT YOUR SCRATCH TESTS CHECK") < prompt.index(
        "It never gives the result for an input the task itself uses"
    )


def test_on_limits_the_stated_choice_in_the_blocked_job(monkeypatch):
    monkeypatch.setenv("EVOLVE_NO_WORKED_ANSWERS", "1")
    job = ec._verifier_blocked_job()
    flat = " ".join(job.split())
    assert "never the result for an input the task itself uses" in flat
    assert "state the choice your reference solution makes" in flat
    # Only that sentence changes: the rest of the job is the original's.
    assert job.replace(ec._BLOCKED_CHOICE_NO_ANSWER, ec._BLOCKED_CHOICE) == ec._VERIFIER_BLOCKED_JOB
