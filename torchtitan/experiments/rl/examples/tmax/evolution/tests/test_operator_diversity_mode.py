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


def test_dockerfile_boilerplate_does_not_pick_the_operator() -> None:
    # Every Dockerfile says WORKDIR and chmod. A seed about diffing two YAML
    # files must not come out as a path or permission task because of it.
    diff_task = {
        "task_id": "t",
        "instruction": "Compare the two YAML config files with gendiff and write the "
                       "diff output to /app/result.txt.",
        "dockerfile": "FROM python:3.11-slim\nCOPY gendiff /usr/local/bin/gendiff\n"
                      "RUN chmod +x /usr/local/bin/gendiff\nWORKDIR /app\n",
        "solution": "gendiff /app/file1.yml /app/file2.yml > /app/result.txt\n",
        "env_files": {"Dockerfile": "", "file1.yml": "", "file2.yml": "", "gendiff": ""},
    }
    with_df = llm.local_fit(diff_task)
    without_df = llm.local_fit({**diff_task, "dockerfile": ""})
    # The Dockerfile may still add structure points, but equally to every
    # operator, so the ranking is the same with and without it.
    rank = lambda fit: [op for op, _ in sorted(fit.items(), key=lambda kv: (-kv[1], kv[0]))]
    assert rank(with_df) == rank(without_df)
    assert rank(with_df)[0] != "path_workdir_alignment"
