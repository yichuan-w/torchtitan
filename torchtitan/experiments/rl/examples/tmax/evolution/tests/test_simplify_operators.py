# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""A simplify declaration must cite real attempts; the prompt offers no menu."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import simplify_operators as so


def decision(pkg, **updates):
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "traces").mkdir(exist_ok=True)
    (pkg / "traces/attempt-01.jsonl").write_text(
        '{"reward":0}\n{"turn":2,"output":"invalid input"}\n'
    )
    row = dict(
        retained_skill="use tool",
        bottleneck="cannot interpret error",
        change="explain input type",
        restore="restore error",
        prediction="The agent can correct the input type; repeating the same invalid call would refute this.",
        expected="3 of 12",
        evidence=[
            dict(
                attempt="attempt-01.jsonl",
                turn=2,
                observation="tool reports invalid input",
            )
        ],
    )
    row.update(updates)
    (pkg / "run/simplify.json").write_text(json.dumps(row))
    return row


def test_record_round_trips_real_evidence(tmp_path):
    row = decision(tmp_path)
    assert so.read_decision(tmp_path, "vague") == row


def test_turn_zero_cites_the_attempts_summary_line(tmp_path):
    row = decision(
        tmp_path, evidence=[dict(attempt="attempt-01.jsonl", turn=0, observation="x")]
    )
    assert so.read_decision(tmp_path, "vague") == row


def test_turn_zero_needs_a_summary_line(tmp_path):
    decision(
        tmp_path, evidence=[dict(attempt="attempt-01.jsonl", turn=0, observation="x")]
    )
    (tmp_path / "traces/attempt-01.jsonl").write_text('{"turn":1,"output":"x"}\n')
    with pytest.raises(ValueError, match="turn absent"):
        so.read_decision(tmp_path, "vague")


def test_prompt_names_the_file_as_listed_and_the_summary_line():
    text = " ".join(so.prompt("vague").split())
    assert "attempt-03.jsonl" in text and "or 0 for its first line" in text
    # A sandbox test of an attempt's implementation is recorded on that attempt.
    assert "evidence item for the attempt whose implementation you ran" in text


def test_attempt_named_without_its_suffix_is_that_file(tmp_path):
    row = decision(
        tmp_path, evidence=[dict(attempt="attempt-01", turn=2, observation="x")]
    )
    out = so.read_decision(tmp_path, "vague")
    assert out["evidence"][0]["attempt"] == "attempt-01.jsonl"
    assert out["retained_skill"] == row["retained_skill"]


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_output_does_not_split_json_records(tmp_path, separator):
    row = decision(tmp_path)
    (tmp_path / "traces/attempt-01.jsonl").write_text(
        json.dumps(
            {"turn": 2, "output": "before" + separator + "after"}, ensure_ascii=False
        )
        + "\n"
    )
    assert so.read_decision(tmp_path, "vague") == row


@pytest.mark.parametrize(
    "field,value",
    [
        ("retained_skill", ""),
        ("prediction", ""),
        ("prediction", None),
        ("expected", ""),
        ("expected", "3/12"),
        ("expected", "about half"),
        ("evidence", []),
        ("evidence", [dict(attempt="../secret", turn=2, observation="x")]),
        ("evidence", [dict(attempt="attempt-01.jsonl", turn=99, observation="x")]),
        ("evidence", [dict(attempt="attempt-02.jsonl", turn=2, observation="x")]),
    ],
)
def test_invalid_declaration_rejected(tmp_path, field, value):
    decision(tmp_path, **{field: value})
    with pytest.raises(ValueError):
        so.read_decision(tmp_path, "vague")


def test_prompt_has_no_menu_and_keeps_the_hint_levels():
    for hint in ("none", "vague", "specific"):
        text = so.prompt(hint)
        assert f"Hint level: {hint}" in text
        assert "Choose the change from the attempts" in text
        assert "add_scaffold" not in text and "operator" not in text
    with pytest.raises(ValueError):
        so.prompt("loud")


def test_prompt_states_the_goal_the_costs_and_the_general_rules():
    text = " ".join(so.prompt("vague").split())
    # The target, and which error costs more.
    assert "lands in the learnable band" in text
    assert "Neither miss is the safe one" in " ".join(text.split())
    # A failed prediction is explained from the traces before the next change.
    assert "Write this comparison in previous" in text
    # Repair is part of the rewrite; nothing asks for a separate repair.
    assert "Repairing it is part of this rewrite" in text
    assert "Do not combine this repair" not in text
    # Guidance is checked against what the reference actually computes.
    assert "Apply your guidance literally" in text
    assert "BLOCKED: invalid_signal:" in text
    flat = " ".join(text.split())
    assert "only when no scored attempt with its trace remains" in flat
    assert "was scored and failed like any other" in flat
    assert 'written as "k of n"' in text


def test_a_repair_is_alone_only_when_it_lets_some_attempt_pass():
    """A repair is measured alone when re-grading shows it moves some attempt;
    otherwise help for the next failing check rides in the same rewrite."""
    p = " ".join(so.prompt("vague").split())
    assert "it is this rewrite's only change" in p
    assert "the repaired task is measured before any help is added" in p
    assert "If none would pass, the repair cannot move the band by itself" in p


def test_an_all_failed_task_gets_a_hint_not_a_give_up():
    p = " ".join(so.prompt("vague").split())
    assert "gives a hint, not up" in p
    assert "only when the task cannot be made valid here" in p


def test_extra_guidance_unset_leaves_prompt_unchanged(monkeypatch):
    monkeypatch.delenv("EVOLVE_SIMPLIFY_EXTRA_GUIDANCE_FILE", raising=False)
    assert so.prompt("vague").rstrip().endswith("keep it unchanged.")


def test_extra_guidance_file_is_appended(monkeypatch, tmp_path):
    extra = tmp_path / "extra.md"
    extra.write_text("## Bench rule\n\nDo not preinstall what the student can install.\n")
    monkeypatch.setenv("EVOLVE_SIMPLIFY_EXTRA_GUIDANCE_FILE", str(extra))
    text = so.prompt("none")
    assert text.index("keep it unchanged.") < text.index("## Bench rule")
    assert text.rstrip().endswith("Do not preinstall what the student can install.")


def test_extra_guidance_missing_file_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("EVOLVE_SIMPLIFY_EXTRA_GUIDANCE_FILE", str(tmp_path / "absent.md"))
    with pytest.raises(FileNotFoundError):
        so.prompt("vague")
