# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Harness tmux clients run from a private copy once pinned."""

import asyncio
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl.harness.agents.terminus import _with_pinned_tmux
from torchtitan.experiments.rl.harness.agents.terminus_terminal import (
    TerminalLifecycle,
)

_PINNED = "/var/tmp/t/tmux/ld-linux-x86-64.so.2 --library-path /var/tmp/t/tmux /var/tmp/t/tmux/tmux"


def test_unpinned_command_is_unchanged() -> None:
    assert _with_pinned_tmux("tmux send-keys -t s ls Enter", "") == (
        "tmux send-keys -t s ls Enter"
    )


def test_pinned_command_defines_a_function_and_rewrites_timeout() -> None:
    out = _with_pinned_tmux("timeout 30s tmux wait done; tmux capture-pane -p", _PINNED)
    assert out.startswith(f'tmux() {{ {_PINNED} "$@"; }}; timeout 30s {_PINNED} wait')
    assert out.endswith("; tmux capture-pane -p")


def _fake(stdout: str, rc: int = 0):
    async def execute(_cmd: str):
        return SimpleNamespace(return_code=rc, stdout=stdout, stderr="")

    return execute


def test_pin_accepts_only_a_loader_in_the_private_directory(monkeypatch) -> None:
    monkeypatch.setenv("TT_PIN_TMUX", "1")
    term = TerminalLifecycle(_fake(""))
    asyncio.run(term.pin_tmux())
    assert term.tmux_command == "" and term.events[-1]["kind"] == "tmux_pin_unavailable"

    term = TerminalLifecycle(None)
    term.execute = _fake(f"{term.directory}/tmux/ld-linux-x86-64.so.2\n")
    asyncio.run(term.pin_tmux())
    assert term.tmux_command.startswith(f"{term.directory}/tmux/ld-linux-x86-64.so.2 ")
    assert term.events[-1]["kind"] == "tmux_pinned"


def test_pin_disabled(monkeypatch) -> None:
    monkeypatch.setenv("TT_PIN_TMUX", "0")
    term = TerminalLifecycle(_fake("/x/tmux/ld-linux.so.2"))
    asyncio.run(term.pin_tmux())
    assert term.tmux_command == "" and not term.events


@pytest.mark.skipif(
    shutil.which("tmux") is None or shutil.which("ldd") is None,
    reason="needs tmux and ldd",
)
def test_pinned_client_survives_a_shadowing_tmux(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "tmux").write_text("#!/bin/sh\nexit 127\n")
    (fake_bin / "tmux").chmod(0o755)

    async def execute(cmd: str):
        p = subprocess.run(
            ["sh", "-c", _with_pinned_tmux(cmd, term.tmux_command)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TMUX_TMPDIR": term.directory},
        )
        return SimpleNamespace(return_code=p.returncode, stdout=p.stdout, stderr=p.stderr)

    term = TerminalLifecycle(execute)
    term.directory = str(tmp_path / "term")
    asyncio.run(term.prepare())
    asyncio.run(term.pin_tmux())
    if not term.tmux_command:
        pytest.skip("no copyable loader on this host")
    asyncio.run(execute("tmux new-session -d -s t 'sleep 30'"))
    try:
        shadowed = subprocess.run(
            ["sh", "-c", _with_pinned_tmux("tmux has-session -t t", term.tmux_command)],
            capture_output=True,
            env={"PATH": f"{fake_bin}:/usr/bin:/bin", "TMUX_TMPDIR": term.directory},
        )
        assert shadowed.returncode == 0
    finally:
        asyncio.run(execute("tmux kill-server"))
