# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""A pane whose foreground program does not read its input stops taking keys.

Text typed at it piles up in the pty's input queue; once that is full, C-c
waits behind the pile and no signal is raised, so the screen never changes
again. The adapter probes the foreground with every key delivery: text that
would only add to the pile is held back, and an interrupt against a full queue
(or repeated against the same program) is raised directly.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.harness.agents import terminus
from torchtitan.experiments.rl.harness.agents.terminus import (
    _SandboxEnvironment,
    _sent_keys,
    _UNFREEZE_MARK,
)


def _env(tmp_path: Path) -> _SandboxEnvironment:
    env = _SandboxEnvironment(AsyncMock(), agent_dir=tmp_path / "agent")
    env.terminal.session = "terminus-1"
    env.terminal.server_pid = "100"
    env.terminal.pane_pid = "200"
    return env


def _send(env, keys: str, probe: str = "", rc: int = 0):
    """Deliver ``keys`` through env.exec; the sandbox answers with ``probe``."""
    scripts = []

    async def exec_raw(script, **kwargs):
        scripts.append(script)
        return SimpleNamespace(return_code=rc, stdout=probe, stderr="")

    env._exec_raw = exec_raw

    async def no_probe():
        return None

    env.terminal._ensure_live = no_probe
    result = asyncio.run(env.exec(f"tmux send-keys -t terminus-1 -- {keys}"))
    return result, scripts


def _probe(pgrp="", unread=0, program="", action=""):
    line = f"{_UNFREEZE_MARK}|{pgrp}|{unread}|{program}\n"
    return line + (f"{_UNFREEZE_MARK}{action}\n" if action else "")


def test_sent_keys_reads_the_key_arguments():
    assert _sent_keys("tmux send-keys -t s -- 'ls -la' Enter") == ["ls -la", "Enter"]
    assert _sent_keys("timeout 5 tmux send-keys -t s C-c") == ["C-c"]
    assert _sent_keys("tmux capture-pane -p") == []


def test_text_is_sent_behind_a_probe_and_the_probe_line_is_stripped(tmp_path):
    env = _env(tmp_path)
    result, scripts = _send(env, "'ls' Enter", probe=_probe() + "ok")
    assert "/proc/$pp/stat" in scripts[0] and "tmux send-keys" in scripts[0]
    assert _UNFREEZE_MARK not in result.stdout
    assert env._take_unfreeze_note() == ""


def test_text_for_a_program_that_left_input_unread_is_held(tmp_path):
    env = _env(tmp_path)
    _send(env, "'cat > x << EOF' Enter", _probe("321", 3529, "python3 loop.py", "held"))
    # Later text this turn is not even offered to the sandbox.
    _, scripts = _send(env, "'echo after' Enter")
    assert scripts == []
    note = env._take_unfreeze_note()
    assert "`python3 loop.py` (pid 321)" in note and "3529 bytes" in note
    assert "characters you typed after that were not sent" in note
    assert env.terminal.events[-1]["kind"] == "keys_held"
    # A new turn types again.
    _, scripts = _send(env, "'ls' Enter", _probe())
    assert len(scripts) == 1


def test_interrupt_still_goes_out_while_text_is_held(tmp_path):
    env = _env(tmp_path)
    _send(env, "'x' Enter", _probe("321", 3600, "loop", "held"))
    _, scripts = _send(env, "C-c", _probe("321", 3600, "loop", "signal|INT"))
    assert len(scripts) == 1 and 'kill -s "$sig"' in scripts[0]
    note = env._take_unfreeze_note()
    assert "sent SIGINT" in note and "unread input (3600 bytes) was discarded" in note
    assert env.terminal.events[-1]["kind"] == "foreground_signaled"


def test_full_queue_is_the_trigger_for_a_first_interrupt(tmp_path):
    env = _env(tmp_path)
    _, scripts = _send(env, "C-c", _probe("321", 10, "loop"))
    script = scripts[0]
    assert f'-ge {terminus._UNFREEZE_FULL_QUEUE_BYTES} ]; then sig=INT' in script
    # No escalation on the first turn.
    assert '[ "$tp" = ' not in script
    assert env._take_unfreeze_note() == ""


def test_unanswered_interrupts_at_one_program_escalate_int_term_kill(tmp_path):
    env = _env(tmp_path)
    seen = []
    for _turn in range(6):
        _, scripts = _send(env, "C-c", _probe("321", 0, "vim -Es"))
        seen.append(scripts[0])
        env._take_unfreeze_note(drew_output=False)
    assert '[ "$tp" = ' not in seen[0] and '[ "$tp" = ' not in seen[1]
    assert "sig=INT; fi" in seen[2].split('[ "$tp" = 321 ]')[1]
    assert "sig=TERM; fi" in seen[3]
    assert "sig=KILL; fi" in seen[4] and "sig=KILL; fi" in seen[5]


def test_a_program_that_answers_interrupts_is_never_escalated(tmp_path):
    # A live guest console (qemu -nographic, telnet) prints after each C-c.
    env = _env(tmp_path)
    for _turn in range(6):
        _, scripts = _send(env, "C-c", _probe("321", 0, "qemu-system-x86_64"))
        assert '[ "$tp" = ' not in scripts[0]
        env._take_unfreeze_note(drew_output=True)


def test_drew_output_ignores_echo_of_typed_text_and_control_keys():
    from torchtitan.experiments.rl.harness.agents.terminus import _drew_output

    assert not _drew_output("^C^C:q!\n", ":q!\n")
    assert not _drew_output("^C^C^C", "")
    assert not _drew_output("", "")
    assert _drew_output("^C\nlocalhost:~# ", "")
    assert _drew_output("KeyboardInterrupt", "")


def test_escalation_counts_unanswered_turns_until_the_foreground_changes(tmp_path):
    env = _env(tmp_path)
    # C-c, then other keys at the same program (vim -Es: C-c, Enter, :q!).
    _send(env, "C-c", _probe("321", 0, "vim -Es"))
    env._take_unfreeze_note(drew_output=False)
    _send(env, "':q!' Enter", _probe("321", 0, "vim -Es"))
    env._take_unfreeze_note(drew_output=False)
    _send(env, "C-c", _probe("321", 0, "vim -Es"))
    env._take_unfreeze_note(drew_output=False)
    _, scripts = _send(env, "C-c", _probe("321", 0, "vim -Es"))
    assert '[ "$tp" = 321 ]; then sig=INT' in scripts[0]
    env._take_unfreeze_note(drew_output=False)
    # The shell came back: the next program starts from zero.
    _send(env, "'ls' Enter", _probe())
    env._take_unfreeze_note()
    for _turn in range(2):
        _, scripts = _send(env, "C-c", _probe("999", 0, "b"))
        env._take_unfreeze_note(drew_output=False)
    assert '[ "$tp" = ' not in scripts[0]


@pytest.mark.parametrize("keys", ["'a' Enter", "C-c"])
def test_lifecycle_sees_the_same_delivery_with_or_without_the_guard(
    tmp_path, monkeypatch, keys
):
    def lifecycle_calls(enabled: bool) -> list:
        monkeypatch.setattr(terminus, "_UNFREEZE", enabled)
        env = _env(tmp_path)
        seen = []

        async def run(command, execute):
            seen.append(command)
            return await execute(command)

        async def exec_raw(script, **kwargs):
            return SimpleNamespace(return_code=0, stdout=_probe(), stderr="")

        env._exec_raw = exec_raw
        env.terminal.run = run
        asyncio.run(env.exec(f"tmux send-keys -t terminus-1 -- {keys}"))
        return seen

    assert lifecycle_calls(True) == lifecycle_calls(False)


def test_off_switch_leaves_deliveries_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(terminus, "_UNFREEZE", False)
    env = _env(tmp_path)
    _, scripts = _send(env, "C-c")
    assert _UNFREEZE_MARK not in scripts[0]


def test_unbound_terminal_is_not_probed(tmp_path):
    env = _env(tmp_path)
    env.terminal.pane_pid = ""
    _, scripts = _send(env, "'ls' Enter")
    assert _UNFREEZE_MARK not in scripts[0]


# --- the probe and the signal path against a real pane ----------------------


@pytest.mark.skipif(
    not (shutil.which("tmux") and shutil.which("perl") and shutil.which("python3")),
    reason="needs tmux, perl and python3",
)
def test_full_queue_interrupt_recovers_a_real_pane(tmp_path):
    sock = tmp_path / "s"
    sock.mkdir()
    run_env = {**os.environ, "TMUX_TMPDIR": str(sock)}

    def sh(cmd: str) -> str:
        return subprocess.run(
            ["bash", "-c", cmd], env=run_env, capture_output=True, text=True, timeout=30
        ).stdout

    sh("tmux new-session -d -s t -x 160 -y 40 'bash --norc --noprofile'")
    try:
        time.sleep(0.5)
        sh("tmux send-keys -t t -- \"python3 -c 'while True: pass'\" Enter")
        time.sleep(1)
        pane_pid = sh("tmux display -p -t t '#{pane_pid}'").strip()
        env = _env(tmp_path)
        env.terminal.pane_pid = pane_pid
        # Two 3.5 KB pastes: the first fills most of the queue, the second is held.
        text = "\n".join(f"row {i:04d} " + "y" * 60 for i in range(50)) + "\n"
        quoted = "'" + text.replace("'", "'\\''") + "'"
        out = sh(env._guard_keys(f"tmux send-keys -t t -- {quoted}", text, ""))
        assert "held" not in out
        time.sleep(0.5)
        out = sh(env._guard_keys(f"tmux send-keys -t t -- {quoted}", text, ""))
        assert f"{_UNFREEZE_MARK}held" in out
        out = sh(env._guard_keys("tmux send-keys -t t C-c", "", "INT"))
        assert f"{_UNFREEZE_MARK}signal|INT" in out
        time.sleep(1)
        sh("tmux send-keys -t t -- 'echo ALIVE_$((40+2))' Enter")
        time.sleep(1)
        screen = sh("tmux capture-pane -p -t t")
        assert any(line.strip() == "ALIVE_42" for line in screen.splitlines())
        # The discarded rows never ran as shell commands.
        assert "row 0000: command not found" not in screen
    finally:
        sh("tmux kill-server")
