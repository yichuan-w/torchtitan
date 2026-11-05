# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The Terminus pane starts in ``_AGENT_SHELL``: DEBIAN_FRONTEND, no history
expansion, ignoreeof -- set on the shell's command line, invisible in the pane."""

from __future__ import annotations

import asyncio
import logging
import os
import pty
import select
import shlex
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("harbor")

from harbor.agents.terminus_2.tmux_session import TmuxSession  # noqa: E402

from torchtitan.experiments.rl.harness.agents import terminus  # noqa: E402


def _harbor_session(name: str = "terminus-2", log: str = "/logs/agent/terminus_2.pane"):
    return TmuxSession(
        session_name=name,
        environment=MagicMock(),
        logging_path=PurePosixPath(log),
        local_asciinema_recording_path=None,
        remote_asciinema_recording_path=None,
    )


def _env() -> terminus._SandboxEnvironment:
    return terminus._SandboxEnvironment(MagicMock(), agent_dir=Path("/tmp/run"))


def test_harbor_pane_start_runs_the_agent_shell():
    start = _harbor_session()._tmux_start_session
    sandbox = MagicMock()
    sandbox.exec = AsyncMock(return_value=(0, "", ""))
    env = terminus._SandboxEnvironment(sandbox, agent_dir=Path("/tmp/run"))
    asyncio.run(env.exec(start))
    sent = sandbox.exec.call_args.args[0]
    tokens = shlex.split(sent)
    at = tokens.index("-s")
    assert tokens[at + 1 : at + 3] == ["terminus-2", terminus._AGENT_SHELL]
    assert terminus._HARBOR_PANE_SHELL not in tokens
    # Only the shell token changed; the pane pipe is rewritten as before.
    assert env._agent_pane_shell(start) == start.replace(
        "'bash --login'", shlex.quote(terminus._AGENT_SHELL), 1
    )
    assert env.pane_path and "dd bs=1" in sent


def test_keys_that_spell_the_start_command_are_not_rewritten():
    session = _harbor_session()
    env = _env()
    keys = [session._tmux_start_session, "Enter", "tmux new-session -s x 'bash --login'"]
    for command in session._tmux_send_keys(keys):
        assert env._agent_pane_shell(command) == command


def test_other_commands_pass_through_untouched(caplog):
    env = _env()
    for command in (
        "tmux has-session -t terminus-2",
        "tmux capture-pane -p -t terminus-2",
        "bash /tests/test.sh",
        "echo 'unbalanced",
    ):
        assert env._agent_pane_shell(command) == command
    assert not caplog.records


def test_an_unrecognized_pane_start_is_left_alone_and_said(caplog):
    env = _env()
    command = "tmux new-session -d -s terminus-2 'zsh -l'"
    with caplog.at_level(logging.WARNING):
        assert env._agent_pane_shell(command) == command
    assert "shell defaults not applied" in caplog.text


def _read_until(fd: int, marker: bytes, timeout: float = 10.0) -> bytes:
    out = b""
    deadline = time.monotonic() + timeout
    while marker not in out and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                out += os.read(fd, 65536)
            except OSError:
                break
    return out


def _spawn(shell: str, home: Path):
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "TERM": "xterm-256color",
            "SHELL": "/bin/bash",
            "PS1": "PROMPT$ ",
        }
        os.execvpe("bash", ["bash", "-c", shell], env)
    return pid, fd


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_agent_shell_sets_all_three_and_survives_ctrl_d(tmp_path):
    pid, fd = _spawn(terminus._AGENT_SHELL, tmp_path)
    try:
        _read_until(fd, b"$ ")
        os.write(fd, b"set -o | grep -E '^(histexpand|ignoreeof)' | tr -s ' \\t' ' '; ")
        # Markers are computed so the terminal's echo of the input never matches.
        os.write(fd, b'echo FE=$DEBIAN_FRONTEND; echo "x!y"; echo END$((40+1))\n')
        out = _read_until(fd, b"END41\r\n").decode(errors="replace")
        assert "histexpand off" in out and "ignoreeof on" in out
        assert "FE=noninteractive" in out
        assert "\r\nx!y\r\n" in out and "event not found" not in out
        os.write(fd, b"\x04")
        _read_until(fd, b"leave the shell", timeout=3)
        os.write(fd, b"echo ALIVE$((40+2))\n")
        assert b"ALIVE42\r\n" in _read_until(fd, b"ALIVE42\r\n")
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
def test_first_pane_screen_is_unchanged(tmp_path):
    """Harbor's start command, run for real: the first capture is byte-identical."""

    def first_screen(shell_command: str, label: str) -> str:
        sock = tmp_path / label
        sock.mkdir()
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TMUX_TMPDIR": str(sock),
            "PS1": "PROMPT$ ",
        }
        start = _harbor_session(label, str(tmp_path / f"{label}.pane"))._tmux_start_session
        if shell_command != "base":
            start = _env()._agent_pane_shell(start)
        if shell_command == "neutral":
            start = _neutral_env(sock)._neutral_tmux_server(start)
        subprocess.run(["bash", "-c", start], env=env, check=True, timeout=30)
        try:
            screen, deadline = "", time.monotonic() + 10
            while "$" not in screen and time.monotonic() < deadline:
                time.sleep(0.1)
                screen = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-t", label],
                    env=env, capture_output=True, text=True, timeout=10,
                ).stdout
            return screen
        finally:
            subprocess.run(["tmux", "kill-server"], env=env, timeout=10)

    base, fixed = first_screen("base", "base"), first_screen("fixed", "fixed")
    assert "$" in base
    assert fixed == base
    assert first_screen("neutral", "neutral") == base


# --- the tmux server under a neutral name (TT_NEUTRAL_TMUX) ---------------


def _neutral_env(directory) -> terminus._SandboxEnvironment:
    env = _env()
    env.terminal.directory = str(directory)
    env.terminal.server_link = f"{directory}/tmux-session"
    env.terminal.prepared = True
    return env


def test_server_starts_through_the_link_only_when_prepared(monkeypatch):
    monkeypatch.delenv("TT_NEUTRAL_TMUX", raising=False)
    start = _harbor_session()._tmux_start_session
    env = _env()
    assert env._neutral_tmux_server(start) == start
    env.terminal.prepared = True
    link = env.terminal.server_link
    assert link.endswith("/tmux-session")
    sent = env._neutral_tmux_server(start)
    assert sent.startswith(f't=tmux; ln -sfn "$(command -v tmux)" {link} 2>/dev/null')
    assert '"$t" new-session ' in sent and "tmux new-session" not in sent
    # The pane's command line is otherwise unchanged.
    assert sent.split('"$t" new-session ', 1)[1] == start.split("tmux new-session ", 1)[1]
    for command in _harbor_session()._tmux_send_keys([start, "Enter"]) + [
        "tmux has-session -t terminus-2",
        "tmux set-option -g history-limit 10000000",
    ]:
        assert env._neutral_tmux_server(command) == command


def test_neutral_server_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("TT_NEUTRAL_TMUX", "0")
    env = _env()
    env.terminal.prepared = True
    start = _harbor_session()._tmux_start_session
    assert env._neutral_tmux_server(start) == start


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
@pytest.mark.skipif(shutil.which("pgrep") is None, reason="needs pgrep")
def test_pkill_server_does_not_match_the_neutral_server(tmp_path):
    sock = tmp_path / "s"
    sock.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMUX_TMPDIR": str(sock)}
    name = f"t{os.getpid()}"
    start = _harbor_session(name, str(tmp_path / "p.pane"))._tmux_start_session
    start = _neutral_env(sock)._neutral_tmux_server(_env()._agent_pane_shell(start))

    def tmux(*args):
        return subprocess.run(
            ["tmux", *args], env=env, capture_output=True, text=True, timeout=10
        )

    subprocess.run(["bash", "-c", start], env=env, check=True, timeout=30)
    try:
        pid = tmux("display-message", "-p", "-t", name, "#{pid}").stdout.strip()
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
        assert "server" not in comm and comm.startswith("tmux-session")
        # `pkill server` picks by the same match as `pgrep server` (never
        # sent for real here: it would hit the host's own servers).
        listed = subprocess.run(["pgrep", "server"], capture_output=True, text=True)
        assert pid not in listed.stdout.split()
        assert tmux("has-session", "-t", name).returncode == 0
    finally:
        tmux("kill-server")
