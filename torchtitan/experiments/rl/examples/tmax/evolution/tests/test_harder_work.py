# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_HARDER_WORK and EVOLVE_HARDER_ESCALATE (harder_work.py): the measured
work target, the work line and its one repair, escalation, and the scaled size
caps. The flags-off prompt is pinned by test_harder_work_default_prompt.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_codex as ec
import harder_work as hw
import task_size as ts
import test_blind_verifier as tb
from test_harder_work_default_prompt import _missed_history, _solved_attempts


@pytest.fixture(autouse=True)
def _flags_unset(monkeypatch):
    for name in ("EVOLVE_HARDER_WORK", "EVOLVE_HARDER_ESCALATE", "EVOLVE_HARDER_TARGET",
                 "EVOLVE_PEER_CONTEXT", "EVOLVE_REWRITE_CONTEXT"):
        monkeypatch.delenv(name, raising=False)


def _stats(tmp_path, **kw):
    class _RW:
        traces = tmp_path / "traces"

    _solved_attempts(_RW, **kw)
    return hw.parent_stats(
        _RW.traces, skip_finish=ec._RULE_TERMINATED, turn_budget=150, secs_budget=7200,
        context_tokens=90112,
    )


# -- parent stats and lineage ------------------------------------------------


def test_parent_stats_measure_the_solved_attempts(tmp_path) -> None:
    s = _stats(tmp_path)
    assert s["solved"] == s["attempts"] == 12
    assert s["turns"] == {"min": 11, "median": 16.5, "max": 22}
    assert s["secs"] == {"min": 100.0, "median": 650.0, "max": 1200.0}
    # The records' own budget_sec wins over the default.
    assert s["budgets"] == {"turns": 150, "secs": 2400, "context_tokens": 90112}
    per_turn = 50 + 1 + 1 + 3 + 200
    assert s["chars"]["min"] == 11 * per_turn and s["chars"]["max"] == 22 * per_turn
    assert s["slack"]["turns"] == round(1 - 22 / 150, 3)
    assert s["slack"]["secs"] == 0.5
    assert len(s["per_attempt"]) == 12 and "per_attempt" not in hw.summary(s)


def test_parent_stats_skip_failed_infra_and_rule_terminated(tmp_path) -> None:
    traces = tmp_path / "traces"
    traces.mkdir()
    rows = [
        {"reward": 1.0, "turns": 9, "secs": 10},
        {"reward": 0.0, "turns": 50, "secs": 10},
        {"reward": 1.0, "turns": 99, "secs": 10, "infra_failed": True},
        {"reward": 1.0, "turns": 99, "secs": 10, "finish_reason": "format_error"},
    ]
    for i, r in enumerate(rows):
        (traces / f"attempt-{i:02d}.jsonl").write_text(json.dumps(r) + "\n")
    s = hw.parent_stats(traces, skip_finish=ec._RULE_TERMINATED, turn_budget=150,
                        secs_budget=2400, context_tokens=1000)
    assert (s["solved"], s["attempts"], s["turns"]["max"]) == (1, 2, 9)
    for p in traces.iterdir():
        p.write_text(json.dumps({"reward": 0.0, "turns": 3}) + "\n")
    assert hw.parent_stats(traces, turn_budget=150, secs_budget=2400, context_tokens=1) is None


def _history(tmp_path, entries) -> Path:
    traces = tmp_path / "traces"
    (traces / "history").mkdir(parents=True)
    (traces / "history" / "index.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    return traces


def _h(job, solved=12, total=12, status="accepted"):
    return {"job": job, "status": status, "measured_next": {"solved": solved, "total": total}}


@pytest.mark.parametrize(
    "entries,misses",
    [
        ([], 0),
        ([_h("harder")], 1),
        ([_h("harder"), _h("harder")], 2),
        ([_h("harder"), _h("easier"), _h("harder")], 1),
        ([_h("harder"), _h("harder", solved=7)], 0),
        ([_h("harder"), _h("harder", status="failed")], 1),
        ([{"job": "harder", "status": "accepted"}], 0),
    ],
)
def test_lineage_misses_counts_the_trailing_all_solved_harders(tmp_path, entries, misses):
    assert hw.lineage_misses(_history(tmp_path, entries)) == misses


def test_no_history_is_no_miss(tmp_path) -> None:
    assert hw.lineage_misses(tmp_path) == 0


# -- target ------------------------------------------------------------------


def test_target_needs_a_flag_and_a_solved_parent(tmp_path, monkeypatch) -> None:
    s = _stats(tmp_path)
    assert hw.target(s, 0) is None and hw.target(s, 3) is None
    monkeypatch.setenv("EVOLVE_HARDER_ESCALATE", "1")
    assert hw.target(s, 0) is None  # escalation alone only acts after a miss
    monkeypatch.setenv("EVOLVE_HARDER_WORK", "2")
    assert hw.target(None, 0) is None


@pytest.mark.parametrize(
    "work,escalate,misses,factor,turns,stacked",
    [
        ("2", "0", 0, 2.0, 33, False),
        ("2", "0", 2, 2.0, 33, False),  # escalation off: misses ignored
        ("2", "1", 0, 2.0, 33, False),
        ("2", "1", 1, 3.0, 50, True),
        ("2", "1", 2, 4.0, 66, True),
        ("2", "1", 5, 4.0, 66, True),
        (None, "1", 1, 1.5, 25, True),
        (None, "1", 2, 2.0, 33, True),
    ],
)
def test_target_factor_and_escalation(tmp_path, monkeypatch, work, escalate, misses,
                                      factor, turns, stacked) -> None:
    if work:
        monkeypatch.setenv("EVOLVE_HARDER_WORK", work)
    monkeypatch.setenv("EVOLVE_HARDER_ESCALATE", escalate)
    t = hw.target(_stats(tmp_path), misses)
    assert (t["factor"], t["target_turns"], t["stacked_allowed"]) == (factor, turns, stacked)
    assert t["caps"] == {"max_added": -(-ts.MAX_ADDED * factor // 1),
                         "max_added_asserts": -(-ts.MAX_ADDED_ASSERTS * factor // 1)}


def test_target_is_capped_by_the_turn_budget(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVOLVE_HARDER_WORK", "10")
    t = hw.target(_stats(tmp_path), 0)
    assert t["capped_by_budget"] and t["target_turns"] == int(0.85 * 150)


@pytest.mark.parametrize("spec", ["0.5", "x", "11", "nan"])
def test_a_malformed_factor_is_refused(monkeypatch, spec) -> None:
    monkeypatch.setenv("EVOLVE_HARDER_WORK", spec)
    with pytest.raises(ValueError, match="EVOLVE_HARDER_WORK"):
        hw.work_factor()


@pytest.mark.parametrize(
    "text,n",
    [
        ("work: 40 turns\n", 40),
        ("Plan.\n\n`work: 33 turns`\n", 33),
        ('- work: "25 turns"\n', 25),
        ("WORK: 1 turn\n", 1),
        ("I expect the work: 40 turns or so\n", None),
        ("work: about 40 turns\n", None),
        ("work: 0 turns\n", None),
        ("no line\n", None),
    ],
)
def test_declared_work_takes_only_the_line(text, n) -> None:
    assert hw.declared_work(text) == n


def test_size_rule_honours_scaled_caps() -> None:
    seed = {"solution_lines": 10, "verifier_asserts": 2}
    new = {"solution_lines": 25, "verifier_asserts": 13}
    assert len(ts.violations(seed, new, require_growth=False)) == 2
    scaled = {**seed, **hw.size_caps(2.0)}
    assert ts.violations(scaled, new, require_growth=False) == [
        "the verifier has 13 assertions against the seed's 2; one requirement is two or "
        "three, so at most 10 more"
    ]


# -- the author's prompt and record ------------------------------------------


def _author(monkeypatch, first, repaired=None, edit_task=False):
    real = ec._run_codex
    seen = []

    def run_codex(run, cwd, prompt, resume=None):
        p = real(run, cwd, prompt, resume)
        if (cwd / "solution").exists():
            seen.append(prompt)
            text = repaired if resume else first
            if text is not None:
                (cwd / "run/hardening.md").write_text(text)
            if resume and edit_task:
                (cwd / "instruction.md").write_text("something else\n")
        return p

    monkeypatch.setattr(ec, "_run_codex", run_codex)
    monkeypatch.setattr(ec, "_session_id", lambda _sd: "sid")
    return seen


@pytest.mark.parametrize("blind", [True, False])
def test_work_flag_gives_the_target_and_records_the_line(tmp_path, monkeypatch, blind):
    monkeypatch.setenv("EVOLVE_HARDER_WORK", "2")
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions: list = []
    tb._wire(monkeypatch, sessions, [])
    if not blind:
        monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "same")
    _solved_attempts(rw)
    seen = _author(monkeypatch, "Plan.\n\nwork: 40 turns\n")
    out = ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    prompt = seen[0]
    assert "THE WORK TARGET" in prompt
    assert "turns       11 / 16.5 / 22   (budget" in prompt
    assert "at least 33 turns against the median 16.5 now" in prompt
    assert "modest" not in prompt
    assert "may grow by at most 16 non-comment lines." in prompt
    assert "the verifier may gain at most 10 assertions" in prompt
    assert "stack two independent" not in prompt
    assert len(seen) == 1  # the line was there: no repair session
    work = out["_harder_work"]
    assert work["declared_turns"] == 40 and work["factor"] == 2.0
    assert work["parent"]["turns"]["median"] == 16.5 and "per_attempt" not in work["parent"]
    assert out["_size_caps"] == {"max_added": 16, "max_added_asserts": 10}
    staged = json.loads((rw.package / "run" / "parent_work.json").read_text())
    assert staged["target"]["target_turns"] == 33 and len(staged["stats"]["per_attempt"]) == 12
    seed = json.loads((rw.package / "run" / "seed_size.json").read_text())
    assert (seed["max_added"], seed["max_added_asserts"]) == (16, 10)


def test_escalation_alone_acts_after_a_miss(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HARDER_ESCALATE", "1")
    rw = tb._rewrite(tmp_path, monkeypatch)
    tb._wire(monkeypatch, [], [])
    _solved_attempts(rw)
    _missed_history(rw)
    seen = _author(monkeypatch, "work: 30 turns\n")
    out = ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert "at least 1.5 times its current turns" in seen[0]
    assert "This rewrite may stack two independent" in seen[0]
    assert "last harder rewrite came back solved by every attempt" in seen[0]
    assert out["_harder_work"]["misses"] == 1 and out["_harder_work"]["escalated"]


def test_escalation_without_a_miss_leaves_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HARDER_ESCALATE", "1")
    rw = tb._rewrite(tmp_path, monkeypatch)
    tb._wire(monkeypatch, [], [])
    _solved_attempts(rw)
    seen = _author(monkeypatch, "no line\n")
    out = ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert "THE WORK TARGET" not in seen[0] and "_harder_work" not in out
    assert len(seen) == 1


def test_a_missing_work_line_goes_back_to_the_author(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HARDER_WORK", "2")
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions: list = []
    tb._wire(monkeypatch, sessions, [])
    _solved_attempts(rw)
    seen = _author(monkeypatch, "about 40 turns\n", "about 40 turns\nwork: 40 turns\n")
    out = ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert [s["role"] for s in sessions] == ["author", "author", "verifier"]
    assert sessions[1]["resume"] == "sid"
    assert seen[1].startswith("The caller cannot read your work estimate")
    assert out["_harder_work"]["declared_turns"] == 40


@pytest.mark.parametrize("edit_task", [False, True])
def test_a_work_line_still_missing_or_a_repair_that_edits_discards(
    tmp_path, monkeypatch, edit_task
):
    monkeypatch.setenv("EVOLVE_HARDER_WORK", "2")
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions: list = []
    tb._wire(monkeypatch, sessions, [])
    _solved_attempts(rw)
    _author(monkeypatch, "lots\n", "work: 40 turns\n" if edit_task else "still lots\n", edit_task)
    error, match = (
        (RuntimeError, "harder work repair changed task files")
        if edit_task
        else (ValueError, "requires a line work")
    )
    with pytest.raises(error, match=match):
        ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert [s["role"] for s in sessions] == ["author", "author"]


def test_both_targets_together(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HARDER_WORK", "2")
    monkeypatch.setenv("EVOLVE_HARDER_TARGET", "3-8")
    rw = tb._rewrite(tmp_path, monkeypatch)
    tb._wire(monkeypatch, [], [])
    _solved_attempts(rw)
    seen = _author(monkeypatch, 'expected: "5 of 12"\nwork: 36 turns\n')
    out = ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert "Aim for 3 to 8 of 12 solved in the next group" in seen[0]
    assert "THE WORK TARGET" in seen[0] and "modest" not in seen[0]
    assert out["_harder_expected"] == "5 of 12" and out["_harder_work"]["declared_turns"] == 36


def test_the_record_keys_reach_rewrite_json() -> None:
    import inspect

    import evolve_ondella as od

    src = inspect.getsource(od)
    for key in ('"harder_expected",', '"harder_work",'):
        assert key in src
