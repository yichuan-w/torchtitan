# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The turn's observation comes from tmux line offsets, in one exec.

Terminus-2 finds "new output" by searching the previous whole-history capture
inside the current one. Through Daytona the whole history cannot travel every
turn, so the adapter notes the history size and cursor row when the turn's
keys go in and captures from that line; a full-screen program or a shrunken
history (a cleared screen) falls back to the visible screen, as the original
did when its search failed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.harness.agents.terminus import _SandboxEnvironment
from torchtitan.experiments.rl.harness.agents.terminus_terminal import (
    TerminalUnavailable,
)


def _env(tmp_path: Path) -> _SandboxEnvironment:
    env = _SandboxEnvironment(AsyncMock(), tmp_path / "agent")
    env.terminal.session = "terminus-1"
    env.terminal.server_pid = "100"
    env.terminal.pane_pid = "200"
    return env


def _result(stdout: str, rc: int = 0):
    return SimpleNamespace(return_code=rc, stdout=stdout, stderr="")


def _observe(env, stdouts):
    calls = []

    async def exec_raw(script, **kwargs):
        calls.append(script)
        return _result(stdouts.pop(0))

    env._exec_raw = exec_raw
    session = SimpleNamespace(_session_name="terminus-1")
    return asyncio.run(env.observe_turn(session)), calls


def test_first_key_delivery_notes_where_the_history_stands(tmp_path):
    env = _env(tmp_path)
    cmd = env._record_turn_start("tmux send-keys -t terminus-1 -- 'ls' Enter")
    assert cmd.startswith("[ -e /dev/shm/.torchtitan_turn_pre.terminus-1 ] || tmux display-message")
    assert "'#{history_size}|#{cursor_y}|#{alternate_on}'" in cmd
    assert cmd.endswith("tmux send-keys -t terminus-1 -- 'ls' Enter")
    # Only key deliveries are turn starts; a capture or a probe is not.
    assert env._record_turn_start("tmux capture-pane -p -t terminus-1") == (
        "tmux capture-pane -p -t terminus-1"
    )
    assert env._record_turn_start("timeout 5s tmux paste-buffer -t terminus-1").startswith("[ -e ")
    # Before the terminal is bound nothing is recorded.
    env.terminal.session = None
    assert env._record_turn_start("tmux send-keys -t x Enter") == "tmux send-keys -t x Enter"


def test_first_observation_is_the_screen_then_new_output_by_offset(tmp_path):
    env = _env(tmp_path)
    first = "62|39|0|100|200|none\nscreen|0\n\n__torchtitan_screen__\nprompt$ \n"
    text, _ = _observe(env, [first])
    assert text == "Current Terminal Screen:\nprompt$ \n"

    second = (
        "68|39|0|100|200|62|39|0\nnew|33\n"
        "prompt$ echo NEW\nNEW\nprompt$ \n"
        "\n__torchtitan_screen__\n...screen...\n"
    )
    text, calls = _observe(env, [second])
    assert text == "New Terminal Output:\nprompt$ echo NEW\nNEW\nprompt$ \n"
    assert len(calls) == 1, "one exec per observation"
    assert "capture-pane -p -t terminus-1 -S" in calls[0]
    obs = [e for e in env.exec_trace if e.get("kind") == "observation"]
    assert [o["shown"] for o in obs] == ["screen", "new"]
    assert obs[1]["history_size"] == "68" and obs[1]["start"] == "33"


def test_no_new_lines_or_screen_mode_shows_the_screen(tmp_path):
    env = _env(tmp_path)
    env._observed_once = True
    nothing_new = "68|39|0|100|200|68|39|0\nnew|39\n\n\n__torchtitan_screen__\nprompt$ \n"
    text, _ = _observe(env, [nothing_new])
    assert text == "Current Terminal Screen:\nprompt$ \n"
    # A full-screen program (alternate_on) or a cleared history decides
    # screen mode in the sandbox; the adapter just honours the mode line.
    cleared = "0|0|0|100|200|68|39|0\nscreen|0\n\n__torchtitan_screen__\nprompt$ \n"
    text, _ = _observe(env, [cleared])
    assert text.startswith("Current Terminal Screen:")


def test_a_replaced_terminal_is_detected_in_the_same_exec(tmp_path):
    env = _env(tmp_path)
    env._observed_once = True
    swapped = "5|1|0|999|888|none\nscreen|0\n\n__torchtitan_screen__\nx\n"
    with pytest.raises(TerminalUnavailable) as raised:
        _observe(env, [swapped])
    assert raised.value.failure_reason == "terminal_identity_changed"


def test_exec_trace_carries_the_sandbox_backend_timings(tmp_path):
    env = _env(tmp_path)
    env._sandbox.last_exec_stats = {
        "inline": True,
        "launch_s": 0.0731,
        "wait_s": 0.0,
        "read_s": 0.0,
        "output_bytes": 12,
        "truncated": False,
        "total_s": 0.08,
    }
    env._trace_exec("echo hi", started_at=1.0, exit_code=0)
    entry = env.exec_trace[-1]
    assert entry["inline"] is True and entry["launch_s"] == 0.073
    assert entry["output_bytes"] == 12 and "total_s" not in entry
