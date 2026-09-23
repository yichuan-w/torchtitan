# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The model is told when its context is running out, and how much is left.

Nothing is added below the warning fraction. The first turn at or past it gets a
warning that says what running out costs; every turn after that gets the
remaining budget. The count is the last reply's prompt plus completion, as the
adapter reports it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from torchtitan.experiments.rl.harness.agents.terminus import (
    _AdapterLLM,
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


def test_a_warning_once_then_the_remaining_budget_every_turn():
    llm = _llm()
    llm.context_tokens = 7000
    first = llm.context_notice()
    assert first.startswith("WARNING: You have used 70% of your context window")
    assert "(7,000 of 10,000 tokens); about 3,000 tokens remain" in first
    assert "the task counts as failed" in first
    assert "mark the task complete" in first

    llm.context_tokens = 8500
    assert llm.context_notice() == "[Context: 85% used, about 1,500 tokens remain]"
    llm.context_tokens = 10400
    assert llm.context_notice() == "[Context: 100% used, about 0 tokens remain]"


def test_zero_turns_the_notice_off():
    llm = _llm(warn_frac=0.0)
    llm.context_tokens = 9999
    assert llm.context_notice() == ""


def test_the_notice_rides_on_the_turns_observation(tmp_path: Path):
    env = _SandboxEnvironment(AsyncMock(), agent_dir=tmp_path / "agent")
    env.terminal.session = "terminus-1"
    env.terminal.server_pid = "100"
    env.terminal.pane_pid = "200"
    notices = ["", "[Context: 80% used, about 2,000 tokens remain]"]
    env.context_notice = lambda: notices.pop(0)
    stdouts = [
        "62|39|0|100|200|none\nscreen|0\n\n__torchtitan_screen__\nprompt$ \n",
        "68|39|0|100|200|62|39|0\nnew|33\nprompt$ ls\nfile\nprompt$ \n"
        "__torchtitan_screen__\nprompt$ \n",
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
        "New Terminal Output:\nprompt$ ls\nfile\nprompt$\n\n"
        "[Context: 80% used, about 2,000 tokens remain]\n"
    )
