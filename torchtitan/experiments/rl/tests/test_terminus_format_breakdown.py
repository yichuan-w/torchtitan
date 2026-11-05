# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Per-category format-error accounting in Terminus's _CountingParser, and the
group metrics the tmax rollouter builds from it."""

import asyncio

import pytest

from torchtitan.experiments.rl.harness.agents.spec import FORMAT_ERROR_CATEGORIES
from torchtitan.experiments.rl.harness.agents.terminus import (
    _AdapterLLM,
    _CountingParser,
)
from torchtitan.experiments.rl.harness.agents.terminus_xml import (
    SafeTerminusXMLParser,
)

_ANALYSIS = "<analysis>The file needs a fix before tests pass.</analysis>"
_PLAN = "<plan>Inspect the file, then edit it.</plan>"


def _resp(commands: str, *, wrap: bool = True) -> str:
    body = f"{_ANALYSIS}\n{_PLAN}\n{commands}"
    return f"<response>\n{body}\n</response>" if wrap else body


VALID = _resp('<commands><keystrokes duration="0.1">ls -la\n</keystrokes></commands>')
DONE = _resp("<commands></commands><task_complete>true</task_complete>")

CASES = {
    "missing_wrapper": _resp(
        '<commands><keystrokes duration="0.1">ls\n</keystrokes></commands>',
        wrap=False,
    ),
    "plain_text": "I will now look at the repository and run ls to see the files.",
    "no_commands": f"<response>{_ANALYSIS}{_PLAN}<commands><keystrokes>ls\n",
    "empty_commands": _resp("<commands></commands>"),
    "silent_noop": _resp("<commands>\nwc -l < a.py\n</commands>"),
    "degenerate": "<response><analysis>ok</analysis></response>",
}


def _parser(llm=None) -> _CountingParser:
    return _CountingParser(SafeTerminusXMLParser(), llm=llm)


def _drive(replies: list[str], llm=None) -> _CountingParser:
    parser = _parser(llm)
    for reply in replies:
        parser.parse_response(reply)
    return parser


@pytest.mark.parametrize("category", sorted(CASES))
def test_each_category(category):
    parser = _drive([VALID, CASES[category]])
    assert parser.format_errors == 1
    assert parser.categories == {c: int(c == category) for c in FORMAT_ERROR_CATEGORIES}


def test_categories_sum_to_format_errors_and_valid_turns_are_not_labelled():
    parser = _drive([VALID, *CASES.values(), DONE])
    assert parser.format_errors == len(CASES)
    assert sum(parser.categories.values()) == parser.format_errors
    assert parser.categories["other"] == 0


def test_a_repeated_error_reply_is_degenerate():
    reply = CASES["plain_text"]
    parser = _drive([reply, reply])
    assert parser.categories["plain_text"] == 1
    assert parser.categories["degenerate"] == 1


def test_streaks_and_repeats():
    bad = CASES["plain_text"]
    parser = _drive(
        [
            CASES["silent_noop"],
            CASES["empty_commands"],
            VALID,
            bad,
            CASES["missing_wrapper"],
            CASES["no_commands"],
            VALID,
            VALID,  # repeat of a valid reply still counts
            bad,
        ]
    )
    assert parser.format_errors == 6
    assert parser.opening_streak == 2
    assert parser.max_streak == 3
    assert parser.repeat_turns == 1
    assert parser.replies == 9
    breakdown = parser.breakdown(truncated_turns=2)
    assert breakdown["opening_streak"] == 2
    assert breakdown["max_streak"] == 3
    assert breakdown["repeat_turns"] == 1
    assert breakdown["replies"] == 9
    assert breakdown["truncated_turns"] == 2


def test_no_valid_turn_makes_the_opening_streak_every_error():
    parser = _drive([CASES["plain_text"], CASES["silent_noop"]])
    assert parser.opening_streak == parser.max_streak == 2


def test_truncated_turn_is_counted_at_the_adapter_and_labelled():
    from harbor.llms.base import OutputLengthExceededError

    truncated = f"<response>{_ANALYSIS}{_PLAN}<commands><keystrokes>cat big"

    class _Adapter:
        async def complete(self, _session_id, _payload):
            return {
                "content": [{"type": "text", "text": truncated}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 100, "output_tokens": 500},
            }

    llm = _AdapterLLM(
        _Adapter(),
        session_id="group=0/rollout=0",
        max_context=63488,
        turn_max_tokens=16384,
    )
    with pytest.raises(OutputLengthExceededError):
        asyncio.run(llm.call(prompt="go"))
    assert llm.truncated_turns == 1

    # Unsalvageable: Terminus-2 re-parses the truncated text for warnings.
    parser = _drive([truncated, VALID], llm=llm)
    assert parser.format_errors == 1
    assert parser.categories["truncated"] == 1
    assert parser.breakdown(llm.truncated_turns)["truncated_turns"] == 1


def test_format_breakdown_metrics():
    from torchtitan.experiments.rl.examples.tmax.rollouter import (
        _format_breakdown_metrics,
    )

    a = _drive([CASES["silent_noop"], CASES["silent_noop"], VALID]).breakdown(1)
    b = _drive([VALID, CASES["plain_text"], VALID]).breakdown(0)
    # None / {} are harnesses with no breakdown and stay out of the means.
    metrics = {
        metric.key: metric.value
        for metric in _format_breakdown_metrics([a, b, None, {}])
    }
    for key in metrics:
        assert key.endswith(("_mean", "_frac", "_max")), key
    assert metrics["rollout/format_error_silent_noop_mean"].value == 0.5
    assert metrics["rollout/format_error_silent_noop_frac"].value == 0.5
    assert metrics["rollout/format_error_degenerate_mean"].value == 0.5
    assert metrics["rollout/format_error_plain_text_frac"].value == 0.5
    assert metrics["rollout/format_error_other_frac"].value == 0.0
    assert metrics["rollout/format_error_opening_streak_mean"].value == 1.0
    assert metrics["rollout/format_error_max_streak_mean"].value == 1.5
    assert metrics["rollout/format_error_max_streak_max"].value == 2.0
    assert metrics["rollout/repeat_turn_frac_mean"].value == pytest.approx(1 / 6)
    assert metrics["rollout/truncated_turns_mean"].value == 0.5
    assert _format_breakdown_metrics([None, {}]) == []


def test_confirmation_declines_are_counted():
    from torchtitan.experiments.rl.examples.tmax.rollouter import (
        _format_breakdown_metrics,
    )

    asked_once = {"replies": 3, "confirm_asks": 1, "confirm_declines": 0}
    declined = {"replies": 9, "confirm_asks": 3, "confirm_declines": 2}
    never = {"replies": 5, "confirm_asks": 0, "confirm_declines": 0}
    metrics = {
        metric.key: metric.value
        for metric in _format_breakdown_metrics([asked_once, declined, never])
    }
    assert metrics["rollout/confirm_asks_mean"].value == pytest.approx(4 / 3)
    # Only the two asked siblings are in the denominator.
    assert metrics["rollout/confirm_declined_frac"].value == 0.5
    assert "rollout/confirm_declined_frac" not in {
        metric.key for metric in _format_breakdown_metrics([never])
    }


def test_adapter_counts_the_confirmation_question():
    from torchtitan.experiments.rl.harness.agents import terminus

    assert terminus._CONFIRMATION_PROMPT in (
        "Current terminal state:\n$ \n\nAre you sure you want to mark the task as "
        "complete? This will trigger your solution to be graded"
    )


def test_terminus_asks_the_adapter_for_plain_text():
    # Without plain_text the adapter cuts the reply at a stray <tool_call>
    # token, hiding a well-formed <commands> block after it.
    payloads = []

    class _Adapter:
        async def complete(self, _session_id, payload):
            payloads.append(payload)
            return {
                "content": [{"type": "text", "text": VALID}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 10},
            }

    llm = _AdapterLLM(
        _Adapter(),
        session_id="group=0/rollout=0",
        max_context=63488,
        turn_max_tokens=16384,
    )
    asyncio.run(llm.call(prompt="go"))
    assert payloads[0]["plain_text"] is True
