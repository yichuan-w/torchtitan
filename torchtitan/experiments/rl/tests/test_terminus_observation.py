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
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.harness.agents.terminus import _SandboxEnvironment
from torchtitan.experiments.rl.harness.agents.terminus_terminal import (
    TerminalUnavailable,
)


def _env(tmp_path: Path) -> _SandboxEnvironment:
    env = _SandboxEnvironment(AsyncMock(), agent_dir=tmp_path / "agent")
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


@pytest.mark.skipif(
    shutil.which("tmux") is None or not Path("/dev/shm").is_dir(),
    reason="needs tmux and /dev/shm, as the sandbox has",
)
def test_output_printed_between_turns_is_the_next_observation(tmp_path):
    """Against a real tmux: what a command prints after one observation and
    before the next turn's keys (the model is choosing them) is in the next
    observation, as Terminus-2's previous-buffer search has it."""
    session = f"obs-test-{os.getpid()}"
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-x", "160", "-y", "40",
                    "bash --norc --noprofile"], check=True)
    try:
        env = _env(tmp_path)
        env.terminal.session = session
        env.terminal.server_pid = env.terminal.pane_pid = None

        async def exec_raw(script, **kwargs):
            p = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            return _result(p.stdout, p.returncode)

        env._exec_raw = exec_raw
        obs = SimpleNamespace(_session_name=session)

        def keys(text):
            cmd = env._record_turn_start(f"tmux send-keys -t {session} {shlex.quote(text)} Enter")
            subprocess.run(["bash", "-c", cmd], check=True)

        asyncio.run(env.observe_turn(obs))  # the first screen
        keys("echo AAA; sleep 2; echo BBB")
        time.sleep(0.8)
        # Output lines only: the echoed command line names both.
        first = asyncio.run(env.observe_turn(obs)).splitlines()
        assert "AAA" in first and "BBB" not in first
        time.sleep(2.5)  # BBB prints while the model chooses its next keys
        keys("")
        time.sleep(0.3)
        assert "BBB" in asyncio.run(env.observe_turn(obs)).splitlines()
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session])
        Path(f"/dev/shm/.torchtitan_turn_pre.{session}").unlink(missing_ok=True)


def test_keys_that_start_with_a_dash_are_not_read_as_flags():
    end = _SandboxEnvironment._end_send_keys_options
    assert end("tmux send-keys -t terminus-1 '-EOF' Enter") == (
        "tmux send-keys -t terminus-1 -- '-EOF' Enter"
    )
    assert end("timeout 5s tmux send-keys -t terminus-1 ls Enter") == (
        "timeout 5s tmux send-keys -t terminus-1 -- ls Enter"
    )
    # Harbor 0.22 writes its own ``--``; adding another would type a literal
    # ``--`` before the command. A model's own ``--`` key follows Harbor's.
    for harbor in (
        "tmux send-keys -t terminus-1 -- 'ls -la' Enter",
        "tmux send-keys -t terminus-1 -- -- Enter",
        "timeout 5s tmux send-keys -t terminus-1 -- '-EOF' Enter",
    ):
        assert end(harbor) == harbor
    # Other tmux commands and compound wrappers stay as they are.
    for cmd in (
        "tmux capture-pane -p -t terminus-1",
        "cd /app && tmux send-keys -t terminus-1 ls",
    ):
        assert end(cmd) == cmd


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
def test_dash_key_reaches_a_real_tmux():
    session = f"dash-test-{os.getpid()}"
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "bash --norc --noprofile"], check=True)
    try:
        raw = f"tmux send-keys -t {session} '-EOF'"
        assert subprocess.run(["bash", "-c", raw], capture_output=True).returncode != 0
        cmd = _SandboxEnvironment._end_send_keys_options(raw)
        subprocess.run(["bash", "-c", cmd], check=True)
        time.sleep(0.3)
        pane = subprocess.run(["tmux", "capture-pane", "-p", "-t", session], capture_output=True, text=True).stdout
        assert "-EOF" in pane
        # Harbor's own shape (Enter first ends the line above): the command
        # arrives without a stray ``--``.
        cmd = _SandboxEnvironment._end_send_keys_options(
            f"tmux send-keys -t {session} -- Enter 'echo dash_ok_$((40+2))' Enter"
        )
        subprocess.run(["bash", "-c", cmd], check=True)
        time.sleep(0.3)
        pane = subprocess.run(["tmux", "capture-pane", "-p", "-t", session], capture_output=True, text=True).stdout
        assert "dash_ok_42" in pane and "--echo" not in pane
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session])


def test_control_command_runs_again_when_its_status_never_arrived(tmp_path):
    sandbox = AsyncMock()
    sandbox.exec.side_effect = [TimeoutError("status unavailable"), (0, "1|2|s\n", "")]
    sandbox.last_exec_stats = {}
    env = _SandboxEnvironment(sandbox, agent_dir=tmp_path / "agent")
    result = asyncio.run(env._terminal_exec("tmux display-message -p x"))
    assert (result.return_code, result.stdout) == (0, "1|2|s\n")
    assert sandbox.exec.await_count == 2


def test_control_command_is_not_rerun_after_other_sandbox_errors(tmp_path):
    from torchtitan.experiments.rl.harness.agents.terminus import (
        _SandboxExecutionError,
    )

    sandbox = AsyncMock()
    sandbox.exec.side_effect = [RuntimeError("sandbox gone"), (0, "", "")]
    env = _SandboxEnvironment(sandbox, agent_dir=tmp_path / "agent")
    with pytest.raises(_SandboxExecutionError):
        asyncio.run(env._terminal_exec("tmux display-message -p x"))
    assert sandbox.exec.await_count == 1


def _unanswered():
    return TimeoutError(
        "daytona exec status unavailable after 250s without replaying the "
        "command body (claim=present relaunches=0 launch exit=0 result='launched\\n')"
    )


def test_harbor_tmux_query_runs_again_when_its_status_never_arrived(tmp_path):
    sandbox = AsyncMock()
    sandbox.exec.side_effect = [_unanswered(), (0, "", "")]
    sandbox.last_exec_stats = {}
    env = _SandboxEnvironment(sandbox, agent_dir=tmp_path / "agent")
    result = asyncio.run(env.exec("tmux has-session -t terminus-1"))
    assert result.return_code == 0
    assert sandbox.exec.await_count == 2
    assert env.unanswered_execs == 1


def test_claimed_keys_are_not_resent_when_their_status_never_arrived(tmp_path):
    sandbox = AsyncMock()
    sandbox.exec.side_effect = [_unanswered(), (0, "", "")]
    sandbox.last_exec_stats = {}
    env = _SandboxEnvironment(sandbox, agent_dir=tmp_path / "agent")
    result = asyncio.run(env.exec("tmux send-keys -t terminus-1 -- 'ls' Enter"))
    assert result.return_code == 0
    assert sandbox.exec.await_count == 1


def test_unclaimed_keys_and_other_commands_still_fail(tmp_path):
    from torchtitan.experiments.rl.harness.agents.terminus import (
        _SandboxExecutionError,
    )

    sandbox = AsyncMock()
    sandbox.exec.side_effect = [
        TimeoutError("daytona exec status unavailable (claim=absent)"),
        _unanswered(),
    ]
    env = _SandboxEnvironment(sandbox, agent_dir=tmp_path / "agent")
    with pytest.raises(_SandboxExecutionError):
        asyncio.run(env.exec("tmux send-keys -t terminus-1 -- 'ls' Enter"))
    with pytest.raises(_SandboxExecutionError):
        asyncio.run(env.exec(r"tmux new-session -d -s x \; pipe-pane -t x 'cat'"))
    assert sandbox.exec.await_count == 2


def test_observation_runs_again_and_shows_the_screen_after_a_lost_status(tmp_path):
    from torchtitan.experiments.rl.harness.agents.terminus import (
        _SandboxExecutionError,
    )

    env = _env(tmp_path)
    env._observed_once = True
    replies = [
        _SandboxExecutionError("lost"),
        _result("68|39|0|100|200|62|39|0\nnew|33\nNEW\n\n__torchtitan_screen__\nSCREEN\n"),
    ]
    replies[0].__cause__ = _unanswered()

    async def exec_raw(script, **kwargs):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    env._exec_raw = exec_raw
    text = asyncio.run(env.observe_turn(SimpleNamespace(_session_name="terminus-1")))
    assert text == "Current Terminal Screen:\nSCREEN\n"
    assert env.unanswered_execs == 1


def test_salvaged_string_reaches_the_parser_as_a_response():
    from harbor.llms.base import LLMResponse

    from torchtitan.experiments.rl.harness.agents.terminus import (
        _return_salvage_as_response,
    )

    agent = SimpleNamespace(_query_llm=AsyncMock(return_value="<response/>"))
    _return_salvage_as_response(agent)
    response = asyncio.run(agent._query_llm())
    assert isinstance(response, LLMResponse) and response.content == "<response/>"
