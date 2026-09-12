# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import pytest

from torchtitan.experiments.rl.harness.agents.terminus_xml import (
    SafeTerminusXMLParser,
    TerminusXMLPlainParser,
)


@pytest.mark.parametrize(
    "literal", ["< >", "<  >", "< \t>", "<>", "<\t>", "<\n>", "< < > >"]
)
@pytest.mark.parametrize("section", ["analysis", "plan", "commands"])
def test_empty_angle_brackets_preserve_response_text(literal, section):
    analysis = literal if section == "analysis" else "Inspect the file."
    plan = literal if section == "plan" else "Print a literal."
    command = f"printf '%s\\n' '{literal if section == 'commands' else 'ok'}'\n"
    response = (
        f"<response><analysis>{analysis}</analysis><plan>{plan}</plan>"
        f'<commands><keystrokes duration="0.1">{command}</keystrokes></commands>'
        "</response>"
    )
    result = SafeTerminusXMLParser().parse_response(response)
    assert not result.error
    assert result.analysis == analysis
    assert result.plan == plan
    assert [(item.keystrokes, item.duration) for item in result.commands] == [
        (command, 0.1)
    ]
    assert not result.is_task_complete


def test_unknown_top_level_tag_is_still_reported():
    result = SafeTerminusXMLParser().parse_response(
        "<response>< ><analysis/><plan/><commands/><unexpected/></response>"
    )
    assert not result.error
    assert "Unknown tag found: <unexpected>" in result.warning


@pytest.mark.parametrize(
    "response",
    [
        '<response><analysis/><plan/><commands><keystrokes duration="1">ls\n</keystrokes></commands></response>',
        "<response><analysis/><plan/><commands/><task_complete>true</task_complete></response>",
        "<response><analysis/><plan/></response>",
        "<response><analysis/><plan/><commands/>",
    ],
)
def test_existing_results_are_unchanged(response):
    assert SafeTerminusXMLParser().parse_response(
        response
    ) == TerminusXMLPlainParser().parse_response(response)


def test_unrelated_parser_errors_propagate(monkeypatch):
    def broken(_self, _content):
        raise IndexError("unrelated defect")

    monkeypatch.setattr(TerminusXMLPlainParser, "_find_top_level_tags", broken)
    with pytest.raises(IndexError, match="unrelated defect"):
        SafeTerminusXMLParser().parse_response(
            "<response><analysis/><plan/><commands/></response>"
        )
