# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Commands are launched detached by one-shot exec and read back from /dev/shm.

No Daytona session is created per command: the Toolbox keeps a session as
directories on the sandbox's own disk, so a task that filled its disk used to
cut the harness off from a sandbox that was still alive. A lost launch
response is retried with the same launch string; the wrapper's claim makes the
duplicate a no-op, so the body never runs twice.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.harness.sandbox import daytona as daytona_mod
from torchtitan.experiments.rl.harness.sandbox.daytona import (
    DaytonaSandbox,
    _build_observable_exec,
)


class _Transient(Exception):
    status_code = 502


class _FakeFS:
    """The status file appears after ``appear_after`` polls; the output is fixed."""

    def __init__(self, status: bytes, output: bytes, *, appear_after: int = 0):
        self.status = status
        self.output = output
        self.appear_after = appear_after
        self.polls = 0

    async def get_file_info(self, path):
        if path.endswith(".status"):
            self.polls += 1
            if self.polls <= self.appear_after:
                raise FileNotFoundError(path)
        return object()

    async def download_file(self, path, timeout=None):
        if path.endswith(".status"):
            if self.polls <= self.appear_after:
                raise FileNotFoundError(path)
            return self.status
        if path.endswith(".output"):
            return self.output
        raise FileNotFoundError(path)

    async def upload_file(self, *args, **kwargs):
        return None


def _sandbox(monkeypatch, *, exec_side_effects, fs, fast=True) -> DaytonaSandbox:
    sandbox = DaytonaSandbox(image="alpine:3.19")
    sandbox._sb = type("SB", (), {})()
    sandbox._sb.process = type("P", (), {})()
    sandbox._sb.process.exec = AsyncMock(side_effect=exec_side_effects)
    sandbox._sb.fs = fs
    if fast:
        # Collapse the polling schedule so a unit test finishes in well under a second.
        monkeypatch.setattr(daytona_mod, "_COMMAND_SUBMIT_DELAYS_SEC", (0.0, 0.01, 0.01))
        monkeypatch.setattr(daytona_mod, "_RESULT_POLL_DELAYS_SEC", (0.01,))
        monkeypatch.setattr(daytona_mod, "_RESULT_POLL_INTERVAL_SEC", 0.01)
        monkeypatch.setattr(daytona_mod, "_COMMAND_KILL_GRACE_SEC", 0)
        monkeypatch.setattr(daytona_mod, "_SESSION_POLL_GRACE_SEC", 0)
    return sandbox


def _run(sandbox: DaytonaSandbox, cmd: str = "true", timeout: int = 1):
    return asyncio.run(sandbox.exec(cmd, timeout=timeout))


def test_launch_is_one_backgrounded_exec_with_state_on_shm():
    observable = _build_observable_exec("echo hi", "k1")
    launch = observable.launch(uploaded=False)
    assert launch.endswith("& echo launched")
    assert launch.startswith("( ")
    assert "</dev/null >/dev/null 2>&1" in launch
    assert observable.status_path.startswith("/dev/shm/")
    assert observable.output_path.startswith("/dev/shm/")
    assert daytona_mod._EXEC_CLAIM_DIR.startswith("/dev/shm/")
    assert daytona_mod._EXEC_OUTPUT_DIR.startswith("/dev/shm/")
    # The claim guards the body inside the wrapper's dispatcher.
    assert daytona_mod._EXEC_CLAIM_DIR in observable.inline_command


def test_result_is_read_from_the_status_and_output_files(monkeypatch):
    fs = _FakeFS(b"3\n", b"hello\n", appear_after=2)
    sandbox = _sandbox(monkeypatch, exec_side_effects=[None], fs=fs)
    rc, out, _ = _run(sandbox, "echo hello; exit 3")
    assert (rc, out) == (3, "hello\n")
    assert sandbox._sb.process.exec.await_count == 1
    assert sandbox.issue_tracker.counts == {}


def test_lost_launch_response_relaunches_the_same_command_once(monkeypatch):
    fs = _FakeFS(b"0\n", b"once\n", appear_after=10**6)  # never via the first poll

    calls: list[str] = []

    async def exec_(command, timeout=None):
        calls.append(command)
        if len(calls) == 1:
            raise _Transient("injected empty HTTP 502 response")
        fs.appear_after = 0  # the relaunch (a no-op on the sandbox) "reveals" the result
        return None

    sandbox = _sandbox(monkeypatch, exec_side_effects=exec_, fs=fs)
    rc, out, _ = _run(sandbox)
    assert (rc, out) == (0, "once\n")
    assert len(calls) == 2 and calls[0] == calls[1], "the relaunch must reuse the launch string"
    counts = sandbox.issue_tracker.counts
    assert counts.get("execute_submit_retry") == 1
    assert counts.get("execute_response_recovered") == 1


def test_permanent_launch_error_is_raised_at_once(monkeypatch):
    class _Permanent(Exception):
        status_code = 400

    fs = _FakeFS(b"0\n", b"", appear_after=10**6)
    sandbox = _sandbox(monkeypatch, exec_side_effects=_Permanent("bad request"), fs=fs)
    with pytest.raises(_Permanent):
        _run(sandbox)
    assert sandbox._sb.process.exec.await_count == 1


def test_every_launch_lost_and_no_result_is_unconfirmed(monkeypatch):
    fs = _FakeFS(b"0\n", b"", appear_after=10**6)
    sandbox = _sandbox(monkeypatch, exec_side_effects=_Transient("502"), fs=fs)
    with pytest.raises(_Transient):
        _run(sandbox, timeout=1)
    counts = sandbox.issue_tracker.counts
    assert counts.get("execute_response_unconfirmed") == 1
    assert "command_status_timeout" not in counts


def test_next_launch_removes_the_previous_result_files(monkeypatch):
    fs = _FakeFS(b"0\n", b"one\n")
    launches: list[str] = []

    async def exec_(command, timeout=None):
        launches.append(command)
        return None

    sandbox = _sandbox(monkeypatch, exec_side_effects=exec_, fs=fs)
    _run(sandbox, "first")
    assert not launches[0].startswith("rm -f")
    first_status, first_output = sandbox._stale_result_paths
    assert first_status.endswith(".status") and first_output.endswith(".output")
    _run(sandbox, "second")
    assert launches[1].startswith(f"rm -f {first_status} {first_output} 2>/dev/null; ")
    # A relaunch of the same command must not carry the cleanup twice.
    assert sandbox._stale_result_paths != (first_status, first_output)
