# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.harness.agents.terminus_terminal import (
    TerminalExited,
    TerminalLifecycle,
    TerminalUnavailable,
)


def result(stdout="", code=0, stderr=""):
    return SimpleNamespace(stdout=stdout, return_code=code, stderr=stderr)


def terminal(*responses):
    execute = AsyncMock(side_effect=responses)
    lifecycle = TerminalLifecycle(execute)
    lifecycle.session = "agent"
    lifecycle.server_pid = "123"
    lifecycle.pane_pid = "456"
    lifecycle.starttime = "999"
    lifecycle.socket = lifecycle.directory + "/tmux-0/default"
    return lifecycle, execute


LIVE = "123|456|0||"
MISSING = result(
    code=1, stderr="error connecting to /socket (No such file or directory)"
)


def test_missing_socket_is_repaired_without_replaying_agent_commands():
    lifecycle, execute = terminal(MISSING, result(), result(LIVE))
    send = AsyncMock(return_value=result())
    asyncio.run(lifecycle.run("tmux send-keys -t agent -- 'append-data' Enter", send))
    assert send.await_count == 1
    repair = execute.call_args_list[1].args[0]
    assert "999" in repair and "kill -USR1 123" in repair
    assert [event["kind"] for event in lifecycle.events] == ["socket_restored"]


def test_failed_identity_check_never_delivers_keys():
    lifecycle, _ = terminal(MISSING, result(code=1))
    send = AsyncMock()
    with pytest.raises(TerminalUnavailable, match="terminal_connection_lost"):
        asyncio.run(lifecycle.run("tmux send-keys -t agent Enter", send))
    send.assert_not_awaited()


def test_api_failure_is_not_retried():
    lifecycle, _ = terminal(result(LIVE))
    send = AsyncMock(side_effect=RuntimeError("502 response unconfirmed"))
    with pytest.raises(RuntimeError, match="502"):
        asyncio.run(lifecycle.run("tmux send-keys -t agent Enter", send))
    assert send.await_count == 1


def test_confirmed_connection_failure_retries_only_the_failed_client():
    lifecycle, _ = terminal(result(LIVE), MISSING, result(), result(LIVE))
    send = AsyncMock(side_effect=[MISSING, result()])
    asyncio.run(lifecycle.run("tmux send-keys -t agent Enter", send))
    assert send.await_count == 2


@pytest.mark.parametrize("separator", [" && ", "; ", "\n"])
def test_compound_client_is_never_replayed(separator):
    lifecycle, _ = terminal(result(LIVE))
    send = AsyncMock(return_value=MISSING)
    response = asyncio.run(
        lifecycle.run(
            "tmux load-buffer /file" + separator + "tmux paste-buffer -t agent", send
        )
    )
    assert response.return_code == 1
    assert send.await_count == 1


@pytest.mark.parametrize(
    "command",
    [
        "tmux has-session -t agent",
        "tmux capture-pane -p -t agent",
        "tmux send-keys -t agent Enter",
        "timeout 5s tmux wait done",
    ],
)
def test_normal_shell_exit_is_detected_before_silent_success(command):
    lifecycle, _ = terminal(result("123|456|1|0|"))
    execute = AsyncMock(return_value=result())
    with pytest.raises(TerminalExited):
        asyncio.run(lifecycle.run(command, execute))
    execute.assert_not_awaited()


def test_sigkill_is_unknown_not_a_policy_verdict():
    lifecycle, _ = terminal(result("123|456|1||9"))
    with pytest.raises(TerminalUnavailable, match="terminal_signal_unknown") as exc:
        asyncio.run(lifecycle.run("tmux has-session -t agent", AsyncMock()))
    assert exc.value.terminal_events[0]["signal"] == "9"


def test_changed_shell_is_not_accepted_as_the_original_session():
    lifecycle, _ = terminal(result("123|789|0||"))
    with pytest.raises(TerminalUnavailable, match="terminal_identity_changed"):
        asyncio.run(lifecycle.run("tmux has-session -t agent", AsyncMock()))


def test_unrelated_command_does_not_probe_terminal():
    lifecycle, probe = terminal()
    execute = AsyncMock(return_value=result())
    asyncio.run(lifecycle.run("printf 'tmux send-keys'", execute))
    probe.assert_not_awaited()


def test_each_terminal_has_a_directory_outside_tmp():
    first, _ = terminal()
    second, _ = terminal()
    assert first.directory != second.directory
    assert first.directory.startswith("/var/tmp/terminus-")
