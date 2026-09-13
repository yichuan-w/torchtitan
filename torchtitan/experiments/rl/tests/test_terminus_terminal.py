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
    lifecycle.pane_starttime = "1000"
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


def test_waits_for_the_shell_exit_status_after_pty_close():
    lifecycle, _ = terminal(result("123|456|1||"), result("123|456|1|0|"))
    with pytest.raises(TerminalExited):
        asyncio.run(lifecycle.run("tmux has-session -t agent", AsyncMock()))


def test_missing_exit_status_remains_unknown_after_bounded_reads():
    lifecycle, probe = terminal(
        *[result("123|456|1||") for _ in range(4)], result(code=1)
    )
    with pytest.raises(TerminalUnavailable, match="terminal_exit_unknown"):
        asyncio.run(lifecycle.run("tmux has-session -t agent", AsyncMock()))
    assert probe.await_count == 5


def zombie_stat(*, state="Z", parent="123", starttime="1000", status="0"):
    fields = [state, parent] + ["0"] * 48
    fields[19], fields[49] = starttime, status
    return "456 (shell with ) spaces) " + " ".join(fields)


@pytest.mark.parametrize("status", [0, 7])
def test_unreaped_normal_exit_is_scored_using_original_process(status):
    lifecycle, _ = terminal(
        *[result("123|456|1||") for _ in range(4)],
        result(zombie_stat(status=str(status << 8))),
    )
    with pytest.raises(TerminalExited, match=f"status {status}"):
        asyncio.run(lifecycle.run("tmux send-keys -t agent Enter", AsyncMock()))
    assert lifecycle.events[-1]["source"] == "proc"


def test_unreaped_sigkill_keeps_cause_unknown():
    lifecycle, _ = terminal(
        *[result("123|456|1||") for _ in range(4)],
        result(zombie_stat(status="9")),
    )
    with pytest.raises(TerminalUnavailable, match="terminal_signal_unknown"):
        asyncio.run(lifecycle.run("tmux has-session -t agent", AsyncMock()))
    assert lifecycle.events[0]["signal"] == "9"


@pytest.mark.parametrize(
    "stat",
    [
        zombie_stat(state="S"),
        zombie_stat(parent="999"),
        zombie_stat(starttime="2000"),
        "456 (bash) Z 123",
    ],
)
def test_unverified_process_exit_cannot_become_a_policy_verdict(stat):
    lifecycle, _ = terminal(*[result("123|456|1||") for _ in range(4)], result(stat))
    with pytest.raises(TerminalUnavailable, match="terminal_exit_unknown"):
        asyncio.run(lifecycle.run("tmux has-session -t agent", AsyncMock()))


def test_changed_shell_is_not_accepted_as_the_original_session():
    lifecycle, _ = terminal(result("123|789|0||"))
    with pytest.raises(TerminalUnavailable, match="terminal_identity_changed") as exc:
        asyncio.run(lifecycle.run("tmux has-session -t agent", AsyncMock()))
    assert exc.value.terminal_events[-1]["expected"] == ["123", "456"]
    assert exc.value.terminal_events[-1]["observed"] == ["123", "789"]


@pytest.mark.parametrize("output", ["", "123|456|"])
def test_missing_identity_output_is_not_reported_as_a_changed_shell(output):
    lifecycle, _ = terminal(result(output))
    send = AsyncMock()
    with pytest.raises(
        TerminalUnavailable, match="terminal_identity_unavailable"
    ) as exc:
        asyncio.run(lifecycle.run("tmux send-keys -t agent Enter", send))
    send.assert_not_awaited()
    assert exc.value.terminal_events[-1]["stdout"] == output


def test_failed_probe_retains_its_result_for_diagnosis():
    lifecycle, _ = terminal(result(LIVE, code=124, stderr="timed out"))
    with pytest.raises(TerminalUnavailable, match="terminal_state_unavailable") as exc:
        asyncio.run(lifecycle.run("tmux has-session -t agent", AsyncMock()))
    event = exc.value.terminal_events[-1]
    assert (event["return_code"], event["stdout"], event["stderr"]) == (
        124,
        LIVE,
        "timed out",
    )


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
