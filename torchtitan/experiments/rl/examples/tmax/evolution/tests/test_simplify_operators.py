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
        ("evidence", []),
        ("evidence", [dict(attempt="../secret", turn=2, observation="x")]),
        ("evidence", [dict(attempt="attempt-01.jsonl", turn=99, observation="x")]),
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


def test_prompt_asks_for_faithful_guidance_and_unstated_choices():
    text = so.prompt("vague")
    # Guidance is checked against what the reference actually computes.
    assert "apply your guidance literally" in text
    # A verifier that depends on a choice the instruction never fixes is an
    # obstacle; stating the choice is allowed and leaves grading unchanged.
    assert "choices the reference solution makes that the instruction does not" in text
    assert "leaves the grading unchanged" in text
