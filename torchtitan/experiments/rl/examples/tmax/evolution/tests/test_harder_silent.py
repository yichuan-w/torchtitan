# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_HARDER_SILENT: off, the harder prompt is the golden default
(test_harder_work_default_prompt); on, exactly its three passages change, alone
and together with EVOLVE_HARDER_TARGET / EVOLVE_HARDER_WORK."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_codex as ec
import test_blind_verifier as tb
import test_harder_work_default_prompt as golden


def _prompt(tmp_path, monkeypatch, env: dict[str, str]) -> str:
    for flag in golden.FLAGS:
        monkeypatch.delenv(flag, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions: list = []
    tb._wire(monkeypatch, sessions, [])
    monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "same")
    golden._solved_attempts(rw)
    try:
        ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    except RuntimeError:
        # TARGET / WORK resume the author for a missing line; only the first
        # session's prompt is under test.
        assert env and sessions
    prompt = sessions[0]["prompt"].replace(str(tmp_path), "<TMP>")
    return re.sub(r"ended at [^\n]*?\(\d+ minutes? from now\)", "ended at <CLOCK>", prompt)


def test_on_replaces_exactly_the_three_passages(tmp_path, monkeypatch) -> None:
    prompt = _prompt(tmp_path, monkeypatch, {"EVOLVE_HARDER_SILENT": "1"})
    expected = (golden.GOLDEN / "same_plain.txt").read_text()
    for old, new in ec._SILENT_REPLACEMENTS:
        assert old in expected and old not in prompt and new in prompt
        expected = expected.replace(old, new)
    assert prompt == expected


@pytest.mark.parametrize("env", [{"EVOLVE_HARDER_TARGET": "3-8"}, {"EVOLVE_HARDER_WORK": "2"}])
def test_composes_with_other_harder_flags(tmp_path, monkeypatch, env) -> None:
    off = _prompt(tmp_path / "off", monkeypatch, env)
    on = _prompt(tmp_path / "on", monkeypatch, {**env, "EVOLVE_HARDER_SILENT": "1"})
    for old, new in ec._SILENT_REPLACEMENTS:
        off = off.replace(old, new)
    assert on == off.replace(str(tmp_path / "off"), str(tmp_path / "on"))


def test_other_guidance_is_untouched(monkeypatch) -> None:
    monkeypatch.setenv("EVOLVE_HARDER_SILENT", "1")
    assert ec._harder_silent_text("calibration text") == "calibration text"
