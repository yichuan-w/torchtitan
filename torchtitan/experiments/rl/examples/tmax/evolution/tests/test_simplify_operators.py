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
    # A grading or replay of an attempt is recorded on that attempt.
    assert "evidence item for the attempt whose state or approach you ran" in text


def test_grading_two_attempts_and_replaying_one_is_required():
    text = " ".join(so.prompt("vague").split())
    assert "Both steps are required" in text
    assert "at least two scored attempts" in text
    assert "list every check that failed, not only the first" in text
    assert "replay at least one attempt on the changed task" in text
    assert "expected rests on the replay" in text


def test_a_replay_starts_from_the_untouched_image():
    """What the instruction says is ready, the built image makes ready: the
    rewriter's replay starts from a fresh reset and starts nothing by hand."""
    text = " ".join(so.prompt("vague").split())
    assert "Start that replay from a fresh ./sandbox reset of the built image" in text
    assert "a finding to fix in the image" in text
    spec = Path(so.__file__).resolve().parent / "agents" / "task_evolution.md"
    harder = " ".join(spec.read_text().split())
    assert "What the instruction says is ready, the untouched image makes ready" in harder
    assert "starts nothing by hand first" in harder


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


def test_a_repair_is_the_rewrites_only_change():
    """Attempts measured the defective task, so a repair is measured alone."""
    p = " ".join(so.prompt("vague").split())
    assert "the repair is this rewrite's only change" in p
    assert "the repaired task is measured before any help is added" in p


def test_a_repair_does_not_leave_the_stage_where_limit_attempts_ran_out():
    p = " ".join(so.prompt("vague").split())
    assert "A repair does not license leaving that stage as it is" in p
    assert "the change adds nothing the student has to read" in p


def _limit_traces(pkg, reasons):
    (pkg / "traces").mkdir(exist_ok=True)
    for i, reason in enumerate(reasons, 1):
        (pkg / f"traces/attempt-{i:02d}.jsonl").write_text(
            json.dumps({"reward": 0, "finish_reason": reason}) + "\n"
            + "".join(json.dumps({"turn": t, "output": "x"}) + "\n" for t in range(1, 6))
        )


def test_budget_is_required_when_most_attempts_ended_at_a_limit(tmp_path):
    decision(tmp_path)
    _limit_traces(tmp_path, ["hit_context_limit", "hit_max_turns", "submit"])
    with pytest.raises(ValueError, match="requires budget"):
        so.read_decision(tmp_path, "vague")


def test_budget_covers_every_limit_attempt_with_recorded_turns(tmp_path):
    decision(tmp_path)
    _limit_traces(tmp_path, ["hit_context_limit", "hit_time_budget", "submit"])
    row = json.loads((tmp_path / "run/simplify.json").read_text())
    row["evidence"] = [dict(attempt="attempt-01.jsonl", turn=2, observation="x")]
    # Five one-character turns: two of them are 2 chars, 0.4 of the attempt.
    row["budget"] = [dict(attempt="attempt-01.jsonl", turns=[2, 3], stage="parse",
                          chars=2, share=0.4)]
    (tmp_path / "run/simplify.json").write_text(json.dumps(row))
    with pytest.raises(ValueError, match="attempt-02.jsonl"):
        so.read_decision(tmp_path, "vague")
    row["budget"].append(dict(attempt="attempt-02", turns=[1, 9], stage="parse",
                              chars=2, share=0.4))
    (tmp_path / "run/simplify.json").write_text(json.dumps(row))
    with pytest.raises(ValueError, match="budget turn absent"):
        so.read_decision(tmp_path, "vague")
    row["budget"][1]["turns"] = [1, 2]
    (tmp_path / "run/simplify.json").write_text(json.dumps(row))
    assert so.read_decision(tmp_path, "vague")["budget"][1]["attempt"] == "attempt-02.jsonl"


def _budget_row(pkg, **entry):
    decision(pkg)
    (pkg / "traces").mkdir(exist_ok=True)
    turns = [
        # output 10 + typed 4 + reasoning 6 = 20
        {"turn": 1, "keystrokes": ["ls", "pw"], "output": "x" * 10,
         "think": "abc", "analysis": "de", "plan": "f"},
        # vanillux/mini-swe style: no keystrokes, the reply under raw = 30 + 10
        {"turn": 2, "raw": "r" * 30, "output": "o" * 10},
        {"turn": 3, "keystrokes": [], "output": "y" * 60},
        {"turn": 4, "keystrokes": ["cat f\n"], "output": "z" * 34},
    ]
    (pkg / "traces/attempt-01.jsonl").write_text(
        json.dumps({"reward": 0, "finish_reason": "hit_context_limit"}) + "\n"
        + "".join(json.dumps(t) + "\n" for t in turns)
    )
    row = json.loads((pkg / "run/simplify.json").read_text())
    row["budget"] = [dict(attempt="attempt-01.jsonl", stage="parse", **entry)]
    (pkg / "run/simplify.json").write_text(json.dumps(row))


def test_turn_chars_counts_output_typed_and_reasoning():
    assert so.turn_chars(
        {"turn": 1, "keystrokes": ["ls", "pw"], "output": "x" * 10,
         "think": "abc", "analysis": "de", "plan": "f"}
    ) == {"output": 10, "typed": 4, "reasoning": 6, "total": 20}
    # No keystrokes: the reply as written is what was typed.
    assert so.turn_chars({"turn": 2, "raw": "r" * 30, "output": "o" * 10, "think": "t"}) == {
        "output": 10, "typed": 30, "reasoning": 1, "total": 41}
    # A closing terminus turn types nothing, even with raw beside it.
    assert so.turn_chars({"turn": 3, "keystrokes": [], "raw": "zz"})["typed"] == 0
    records = [{"reward": 0}, {"turn": 1, "output": "ab"}, {"turn": 2, "output": "cdef"}]
    assert so.stage_chars(records, 2, 2) == (4, 6)
    assert [t for t, _ in so.attempt_chars(records)] == [1, 2]


def test_budget_chars_and_share_are_measured_not_guessed(tmp_path):
    # Attempt total 20 + 40 + 60 + 40 = 160; turns 1-2 hold 60, share 0.375.
    _budget_row(tmp_path, turns=[1, 2], chars=60, share=0.375)
    so.read_decision(tmp_path, "vague")
    for chars, share in ((90, 0.375), (60, 0.6)):
        _budget_row(tmp_path, turns=[1, 2], chars=chars, share=share)
        with pytest.raises(ValueError, match="as measured"):
            so.read_decision(tmp_path, "vague")
    for missing in ({"chars": 60}, {"share": 0.375}, {"chars": "60", "share": 0.375}):
        _budget_row(tmp_path, turns=[1, 2], **missing)
        with pytest.raises(ValueError, match="requires chars"):
            so.read_decision(tmp_path, "vague")


def test_a_budget_stage_over_half_the_attempt_is_rejected(tmp_path):
    # Turns 2-4 hold 140 of 160 characters: the whole attempt, not a stage.
    _budget_row(tmp_path, turns=[2, 4], chars=140, share=0.875)
    with pytest.raises(ValueError, match="at most 50%"):
        so.read_decision(tmp_path, "vague")
    # One turn of 60 characters is 0.375 of the attempt: a stage.
    _budget_row(tmp_path, turns=[3, 3], chars=60, share=0.375)
    so.read_decision(tmp_path, "vague")


def test_chars_helper_prints_per_turn_counts_and_the_range(tmp_path, capsys):
    _budget_row(tmp_path, turns=[1, 2], chars=60, share=0.375)
    assert so.print_chars(tmp_path / "traces/attempt-01.jsonl", (1, 2)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split("\t") == ["turn", "output", "typed", "reasoning", "total",
                                   "cumulative", "share"]
    assert out[1].split("\t") == ["1", "10", "4", "6", "20", "20", "0.125"]
    assert out[4].split("\t")[-2:] == ["160", "1.000"]
    assert out[5] == "attempt: 160 chars over 4 turns"
    assert out[6] == "turns 1-2: chars 60 share 0.375"
    so.print_chars(tmp_path / "traces/attempt-01.jsonl", (2, 4))
    assert "not a stage" in capsys.readouterr().out


def test_sandbox_chars_runs_without_a_container(tmp_path):
    import subprocess

    _budget_row(tmp_path, turns=[1, 2], chars=60, share=0.375)
    here = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, str(here / "agent_sandbox.py"), "--pkg", str(tmp_path),
         "chars", "traces/attempt-01.jsonl", "--turns", "1", "2"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "turns 1-2: chars 60 share 0.375" in out


def test_prompt_asks_for_measured_chars_and_a_stage_under_half():
    p = " ".join(so.prompt("vague").split())
    assert "./sandbox chars traces/attempt-03.jsonl" in p
    assert "more than half of the attempt's characters is rejected" in p


def test_budget_is_not_required_when_few_attempts_ended_at_a_limit(tmp_path):
    decision(tmp_path)
    _limit_traces(tmp_path, ["hit_context_limit", "submit", "submit"])
    so.read_decision(tmp_path, "vague")


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


def test_removing_work_delivers_results_not_code_to_extend():
    p = " ".join(so.prompt("vague").split())
    assert "Removing work means delivering its result" in p
    assert "the dependencies installed, the artifacts built" in p
    assert "Code the student must read, adapt or extend does not remove the stage" in p
    assert "a separate file the student calls and the instruction tells it not to edit" in p
    assert "A change only the grader sees" in p
    assert "never counts as the shortening" in p
    # The shared limits paragraph says the same in short.
    budget = " ".join(so.student_budget().split())
    assert "Removing work means delivering its result" in budget
    assert "a starter or helper the student must read or extend" in budget
