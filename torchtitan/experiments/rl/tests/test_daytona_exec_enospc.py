# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""A full sandbox fails an exec at the Toolbox's log-directory mkdir, not only at
session create. That error arrives as a plain API exception after the session
exists and used to be filed as ``exec_failed``, which the rollouter cannot tell
from a dead runner; it is disk exhaustion and is recorded as such."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox


def _exec_raising(message: str) -> DaytonaSandbox:
    sandbox = DaytonaSandbox(image="alpine:3.19")
    sandbox._session_exec = AsyncMock(side_effect=RuntimeError(message))
    with pytest.raises(RuntimeError):
        asyncio.run(sandbox.exec("true"))
    return sandbox


def test_log_directory_enospc_is_disk_exhaustion():
    sandbox = _exec_raising(
        "Failed to execute session command: bad request: failed to create log "
        "directory: mkdir /root/.daytona/sessions/a/b: no space left on device"
    )
    assert sandbox.issue_tracker.counts == {"command_disk_exhausted": 1}


def test_other_exec_errors_stay_exec_failed():
    sandbox = _exec_raising("Failed to execute session command: 502 Bad Gateway")
    assert sandbox.issue_tracker.counts == {"exec_failed": 1}
