# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The model is told when its context is running out, and how much is left.

Nothing is added below the warning fraction. The first turn past it, and the
first past the final fraction, get a full warning with the stakes; every other
turn gets one line with the share used and the tokens left. Each notice is
labelled as coming from the harness and sits before the terminal output. The
count is the last reply's prompt plus completion, as the adapter reports it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from torchtitan.experiments.rl.harness.agents.terminus import (
    _AdapterLLM,
    _CONTEXT_NOTICE_LABEL,
    _SandboxEnvironment,
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


def test_the_first_turn_past_the_fraction_gets_the_full_warning():
    llm = _llm()
    llm.context_tokens = 7000
    notice = llm.context_notice()
    assert notice == (
        f"{_CONTEXT_NOTICE_LABEL} You have used 70% of your context window "
        "(7,000 of 10,000 tokens); about 3,000 tokens remain. If it runs out, the "
        "episode ends before you can submit and the task counts as failed. Avoid "
        "commands that print large output, finish the essential work, verify it, "
        "and mark the task complete."
    )


def test_later_turns_get_one_line_and_the_final_fraction_warns_again():
    llm = _llm()
    llm.context_tokens = 7000
    llm.context_notice()
    llm.context_tokens = 8500
    assert llm.context_notice() == (
        f"{_CONTEXT_NOTICE_LABEL} Context: 85% used, about 1,500 tokens remain."
    )
    llm.context_tokens = 9100
    assert "the task counts as failed" in llm.context_notice()
    llm.context_tokens = 10400
    assert llm.context_notice() == (
        f"{_CONTEXT_NOTICE_LABEL} Context: 100% used, about 0 tokens remain."
    )


def test_jumping_past_both_fractions_warns_once():
    llm = _llm()
    llm.context_tokens = 9500
    assert "the task counts as failed" in llm.context_notice()
    llm.context_tokens = 9600
    assert llm.context_notice().endswith("about 400 tokens remain.")


def test_a_warning_fraction_above_the_final_one_warns_once():
    llm = _llm(warn_frac=0.95)
    llm.context_tokens = 9000
    assert llm.context_notice() == ""
    llm.context_tokens = 9500
    assert "the task counts as failed" in llm.context_notice()
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
