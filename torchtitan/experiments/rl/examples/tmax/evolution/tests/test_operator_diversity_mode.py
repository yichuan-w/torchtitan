"""Which spreading terms apply, and which side of the loop gets them by default."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import synth_client as llm

SEED = {
    "task_id": "t",
    "instruction": "Repair the failing unit tests and rebuild the package with make.",
    "dockerfile": "FROM python:3.11\nRUN pip install pytest\n",
    "solution": "make build && pytest -q\n",
    "env_files": {"Makefile": "", "tests/test_x.py": ""},
}


def _scores(mode: str, used_fams: dict[str, int], used_ops=None) -> dict[str, float]:
    return {op: s for s, _, op in llm.score_operators(SEED, used_ops or {}, used_fams, mode)}


def test_family_balance_moves_scores_only_in_family_plus_freq() -> None:
    # One family far ahead of its share: under "family+freq" its operators are
    # damped, under the other two the counts are not consulted at all.
    ahead = {"build_test_execution_workflow": 40}
    for mode in ("freq", "off"):
        assert _scores(mode, ahead) == _scores(mode, {}), mode
    assert _scores("family+freq", ahead) != _scores("family+freq", {})


def test_operator_frequency_still_damps_a_repeat_unless_off() -> None:
    top = max(_scores("freq", {}).items(), key=lambda kv: kv[1])[0]
    assert _scores("freq", {}, {top: 3})[top] < _scores("freq", {})[top]
    assert _scores("off", {}, {top: 3})[top] == _scores("off", {})[top]


def test_evolution_defaults_to_freq_and_the_env_overrides_it(monkeypatch) -> None:
    monkeypatch.delenv("SWE_OPERATOR_DIVERSITY", raising=False)
    assert llm._diversity_mode(None) == llm.EVOLUTION_DIVERSITY_DEFAULT == "freq"
    monkeypatch.setenv("SWE_OPERATOR_DIVERSITY", "family+freq")
    assert llm._diversity_mode(None) == "family+freq"
    # An argument still wins over the environment.
    assert llm._diversity_mode("off") == "off"


def test_an_unknown_mode_is_refused_rather_than_silently_ignored(monkeypatch) -> None:
    monkeypatch.setenv("SWE_OPERATOR_DIVERSITY", "balanced")
    try:
        llm._diversity_mode(None)
    except ValueError as e:
        assert "balanced" in str(e)
    else:
        raise AssertionError("an unknown mode has to fail loudly")


def test_the_shortlist_evolution_reads_leaves_the_family_term_out(monkeypatch) -> None:
    monkeypatch.delenv("SWE_OPERATOR_DIVERSITY", raising=False)
    ahead = {"build_test_execution_workflow": 40}
    assert (llm.operator_shortlist(SEED, {}, ahead) == llm.operator_shortlist(SEED, {}, {}))
    # Synthesis keeps the full pressure, so the same counts do change its ranking.
    assert (llm.score_operators(SEED, {}, ahead, "family+freq")
            != llm.score_operators(SEED, {}, {}, "family+freq"))
