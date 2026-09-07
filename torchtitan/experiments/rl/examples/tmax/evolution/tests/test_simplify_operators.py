# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""A simplify choice must refer to real attempts and respect the hint mode."""

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
        operator="enhance_feedback",
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("operator", "invented"),
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


def test_none_excludes_guidance(tmp_path):
    decision(tmp_path)
    with pytest.raises(ValueError, match="not allowed"):
        so.read_decision(tmp_path, "none")
    assert "enhance_feedback:" not in so.prompt("none")
    assert "add_scaffold:" not in so.prompt("none")
    assert "Hint level: vague" in so.prompt("vague")
