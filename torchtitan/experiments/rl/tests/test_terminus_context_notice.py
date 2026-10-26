# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The model is told when its context is running out, and how much is left.

Nothing is added below the warning fraction. The first turn past it, and the
first past 75% and 90%, get a full warning with the stakes; every other
turn gets one line with the share used and the tokens left. Each notice is
labelled as coming from the harness and sits before the terminal output. The
count is the last reply's prompt plus completion, as the adapter reports it.
"""

from __future__ import annotations

import asyncio

import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from torchtitan.experiments.rl.harness.agents.terminus import (
    _AdapterLLM,
    _CONTEXT_NOTICE_LABEL,
    _SandboxEnvironment,
)


@pytest.fixture(autouse=True)
def _legacy_notice_style(monkeypatch):
    # These tests cover the opt-in v2 notice; della tests override this below.
    monkeypatch.setattr(
        "torchtitan.experiments.rl.harness.agents.terminus._CONTEXT_NOTICE_STYLE",
        "v2",
    )


def _llm(*, warn_frac=0.7, max_context=10000, usages=()):
    usages = list(usages)

    class _Adapter:
        def session_max_tokens(self, _session_id):
            return None

        async def complete(self, _session_id, _payload):
            in_tok, out_tok = usages.pop(0)
            return {
                "content": [{"type": "text", "text": "<response/>"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
            }

    return _AdapterLLM(
        _Adapter(),
        session_id="group=-1/rollout=0",
        max_context=max_context,
        turn_max_tokens=4096,
        context_warn_frac=warn_frac,
    )


def test_the_count_is_the_last_replys_prompt_plus_completion():
    llm = _llm(usages=[(1200, 300), (2000, 150)])
    asyncio.run(llm.call(prompt="go"))
    assert llm.context_tokens == 1500
    asyncio.run(llm.call(prompt="go"))
    assert llm.context_tokens == 2150


def test_nothing_below_the_warning_fraction():
    llm = _llm()
    llm.context_tokens = 6999
    assert llm.context_notice() == ""


FULL = "the task counts as failed"


def test_the_first_turn_past_the_fraction_gets_the_full_warning():
    llm = _llm()
    llm.context_tokens = 7000
    assert llm.context_notice() == (
        f"{_CONTEXT_NOTICE_LABEL} You have used 70% of your context window "
        "(7,000 of 10,000 tokens); about 3,000 tokens remain. If it runs out, the "
        "episode ends before you can submit and the task counts as failed. Stop "
        "exploring: do not rewrite whole files, write long debug scripts, or run "
        "commands that print large output. Make small targeted edits, run the "
        "check once, then mark the task complete."
    )


def test_one_line_between_warnings_and_full_again_at_75_and_90():
    llm = _llm(warn_frac=0.5)
    llm.context_tokens = 5000
    assert FULL in llm.context_notice()
    llm.context_tokens = 6000
    assert llm.context_notice() == (
        f"{_CONTEXT_NOTICE_LABEL} Context: 60% used, about 4,000 tokens remain."
    )
    llm.context_tokens = 7500
    assert FULL in llm.context_notice()
    llm.context_tokens = 8000
    assert llm.context_notice().endswith("about 2,000 tokens remain.")
    llm.context_tokens = 9000
    assert FULL in llm.context_notice()
    llm.context_tokens = 10400
    assert llm.context_notice() == (
        f"{_CONTEXT_NOTICE_LABEL} Context: 100% used, about 0 tokens remain."
    )


def test_thresholds_use_the_shown_percentage():
    llm = _llm(warn_frac=0.5)
    llm.context_tokens = 5000
    llm.context_notice()
    llm.context_tokens = 8960  # shows 90%
    notice = llm.context_notice()
    assert "You have used 90%" in notice and FULL in notice


def test_jumping_past_several_fractions_warns_once():
    llm = _llm(warn_frac=0.5)
    llm.context_tokens = 9500
    assert FULL in llm.context_notice()
    llm.context_tokens = 9600
    assert llm.context_notice().endswith("about 400 tokens remain.")


def test_a_warning_fraction_above_the_repeats_warns_once():
    llm = _llm(warn_frac=0.95)
    llm.context_tokens = 9400
    assert llm.context_notice() == ""
    llm.context_tokens = 9500
    assert FULL in llm.context_notice()
    llm.context_tokens = 9700
    assert llm.context_notice().endswith("about 300 tokens remain.")


def test_zero_turns_the_notice_off():
    llm = _llm(warn_frac=0.0)
    llm.context_tokens = 9999
    assert llm.context_notice() == ""


def test_the_notice_rides_on_the_turns_observation(tmp_path: Path):
    env = _SandboxEnvironment(AsyncMock(), agent_dir=tmp_path / "agent")
    env.terminal.session = "terminus-1"
    env.terminal.server_pid = "100"
    env.terminal.pane_pid = "200"
    notices = ["", "[MODEL CONTEXT BUDGET]", "[MODEL CONTEXT BUDGET]"]
    env.context_notice = lambda: notices.pop(0)
    stdouts = [
        "62|39|0|100|200|none\nscreen|0\n\n__torchtitan_screen__\nprompt$ \n",
        "68|39|0|100|200|62|39|0\nnew|33\nprompt$ ls\nfile\nprompt$ \n"
        "__torchtitan_screen__\nprompt$ \n",
        "68|39|0|100|200|none\nscreen|0\n\n__torchtitan_screen__\nprompt$ \n",
    ]

    async def exec_raw(_script, **_kwargs):
        return SimpleNamespace(return_code=0, stdout=stdouts.pop(0), stderr="")

    env._exec_raw = exec_raw
    session = SimpleNamespace(_session_name="terminus-1")

    # Below the fraction the observation is exactly what it was.
    assert asyncio.run(env.observe_turn(session)) == (
        "Current Terminal Screen:\nprompt$ \n"
    )
    assert asyncio.run(env.observe_turn(session)) == (
        "[MODEL CONTEXT BUDGET]\n\n"
        "New Terminal Output:\nprompt$ ls\nfile\nprompt$ "
    )
    assert env.exec_trace[-1]["shown"] == "new"
    assert asyncio.run(env.observe_turn(session)) == (
        "[MODEL CONTEXT BUDGET]\n\nCurrent Terminal Screen:\nprompt$ \n"
    )
    assert env.exec_trace[-1]["shown"] == "screen"


def test_della_style_is_one_warning_then_status_lines(monkeypatch):
    """TMAX_CONTEXT_NOTICE_STYLE=della: commit 882db3e0's text, warned once."""
    from torchtitan.experiments.rl.harness.agents import terminus

    monkeypatch.setattr(terminus, "_CONTEXT_NOTICE_STYLE", "della")
    llm = _llm(warn_frac=0.5)
    llm.context_tokens = 5000
    assert llm.context_notice() == (
        "WARNING: You have used 50% of your context window (5,000 of 10,000 "
        "tokens); about 5,000 tokens remain. If it runs out, the episode ends "
        "before you can submit and the task counts as failed. Avoid commands that "
        "print large output, finish the essential work, verify it, and mark the "
        "task complete."
    )
    for used, line in [
        (7600, "[Context: 76% used, about 2,400 tokens remain]"),
        (9100, "[Context: 91% used, about 900 tokens remain]"),
    ]:
        llm.context_tokens = used
        assert llm.context_notice() == line


def test_urge_style_is_della_without_the_failure_stakes(monkeypatch):
    """TMAX_CONTEXT_NOTICE_STYLE=urge: della's timing and call to mark the task
    complete, without saying running out counts as failed."""
    from torchtitan.experiments.rl.harness.agents import terminus

    monkeypatch.setattr(terminus, "_CONTEXT_NOTICE_STYLE", "urge")
    llm = _llm(warn_frac=0.5)
    llm.context_tokens = 4000
    assert llm.context_notice() == ""
    llm.context_tokens = 5000
    assert llm.context_notice() == (
        "WARNING: You have used 50% of your context window (5,000 of 10,000 "
        "tokens); about 5,000 tokens remain. Avoid commands that print large "
        "output, finish the essential work, verify it, and mark the task complete."
    )
    llm.context_tokens = 7600
    assert llm.context_notice() == "[Context: 76% used, about 2,400 tokens remain]"
    llm.context_tokens = 8100
    assert llm.context_notice() == (
        "[Context: 81% used, about 1,900 tokens remain. You are almost out of "
        "context: run as few commands as you can and mark the task complete soon.]"
    )


def test_checked_style_drops_the_call_to_mark_complete(monkeypatch):
    """The default: della's placement and status lines, but a failing submission
    is said to score like running out instead of being asked for."""
    from torchtitan.experiments.rl.harness.agents import terminus

    monkeypatch.setattr(terminus, "_CONTEXT_NOTICE_STYLE", "checked")
    monkeypatch.setattr(terminus, "_CONTEXT_WARN_TOKENS_LEFT", 3000)
    llm = _llm()
    llm.context_tokens = 7000
    assert llm.context_notice() == (
        "WARNING: You have used 70% of your context window (7,000 of 10,000 "
        "tokens); about 3,000 tokens remain. If it runs out, the episode ends "
        "before you can submit and the task counts as failed. Avoid commands that "
        "print large output and keep working efficiently. A submission that fails the "
        "tests scores the same as running out, so mark the task complete only once "
        "your checks pass."
    )


@pytest.mark.parametrize("max_context", [100_000, 200_000])
def test_checked_style_warns_on_tokens_left_whatever_the_window(monkeypatch, max_context):
    """The same tokens-left thresholds for an 80K training and a 160K eval window:
    full warning at the first and repeat thresholds, status lines in between."""
    from torchtitan.experiments.rl.harness.agents import terminus

    monkeypatch.setattr(terminus, "_CONTEXT_NOTICE_STYLE", "checked")
    monkeypatch.setattr(terminus, "_CONTEXT_WARN_TOKENS_LEFT", 30_000)
    monkeypatch.setattr(terminus, "_CONTEXT_REPEAT_TOKENS_LEFT", 10_000)
    llm = _llm(max_context=max_context)
    notices = []
    for left in (40_000, 28_000, 20_000, 9_000, 5_000):
        llm.context_tokens = max_context - left
        notices.append(llm.context_notice())
    assert notices[0] == ""
    assert [n.startswith("WARNING:") for n in notices[1:]] == [True, False, True, False]
    assert notices[2].startswith("[Context:")
    assert "about 9,000 tokens remain" in notices[3]


def test_checked_style_warns_once_when_both_thresholds_pass_together(monkeypatch):
    from torchtitan.experiments.rl.harness.agents import terminus

    monkeypatch.setattr(terminus, "_CONTEXT_NOTICE_STYLE", "checked")
    monkeypatch.setattr(terminus, "_CONTEXT_WARN_TOKENS_LEFT", 3000)
    monkeypatch.setattr(terminus, "_CONTEXT_REPEAT_TOKENS_LEFT", 1000)
    llm = _llm()
    llm.context_tokens = 9500
    assert llm.context_notice().startswith("WARNING:")
    llm.context_tokens = 9600
    assert llm.context_notice().startswith("[Context:")


def test_della_style_goes_after_the_observation(tmp_path: Path, monkeypatch):
    from torchtitan.experiments.rl.harness.agents import terminus

    monkeypatch.setattr(terminus, "_CONTEXT_NOTICE_STYLE", "della")
    env = _SandboxEnvironment(AsyncMock(), agent_dir=tmp_path / "agent")
    env.terminal.session = "terminus-1"
    env.terminal.server_pid = "100"
    env.terminal.pane_pid = "200"
    env.context_notice = lambda: "[Context: 60% used, about 4,000 tokens remain]"
    stdouts = [
        "62|39|0|100|200|none\nscreen|0\n\n__torchtitan_screen__\nprompt$ \n",
    ]

    async def exec_raw(_script, **_kwargs):
        return SimpleNamespace(return_code=0, stdout=stdouts.pop(0), stderr="")

    env._exec_raw = exec_raw
    session = SimpleNamespace(_session_name="terminus-1")
    assert asyncio.run(env.observe_turn(session)) == (
        "Current Terminal Screen:\nprompt$\n\n"
        "[Context: 60% used, about 4,000 tokens remain]\n"
    )


def test_status_lines_off_keeps_only_the_della_warning(monkeypatch):
    from torchtitan.experiments.rl.harness.agents import terminus

    monkeypatch.setattr(terminus, "_CONTEXT_NOTICE_STYLE", "della")
    monkeypatch.setattr(terminus, "_CONTEXT_STATUS_LINES", False)
    llm = _llm(warn_frac=0.5)
    llm.context_tokens = 5000
    assert llm.context_notice().startswith("WARNING: You have used 50%")
    for used in (7600, 9100):
        llm.context_tokens = used
        assert llm.context_notice() == ""
