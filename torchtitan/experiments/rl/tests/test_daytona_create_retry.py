# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Create retries preserve transient recovery without repeating a broken build."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.harness.sandbox import daytona as daytona_mod
from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox


@pytest.mark.parametrize(
    ("errors", "expected_kinds"),
    [
        (
            [
                RuntimeError(
                    "SandboxState.BUILD_FAILED: process /bin/sh -c false "
                    "did not complete successfully: exit code: 1"
                )
            ],
            ["create_failed"],
        ),
        (
            [RuntimeError("transient 401"), SimpleNamespace(id="sb")],
            ["create_retry"],
        ),
    ],
)
def test_create_retry_stops_on_build_failure_but_recovers_transient_error(
    monkeypatch, errors, expected_kinds
):
    client = SimpleNamespace(create=AsyncMock(side_effect=errors))

    async def get_client(**_kwargs):
        return client

    monkeypatch.setattr(daytona_mod, "_get_shared_client", get_client)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setenv("DAYTONA_API_KEY", "test-key")
    monkeypatch.setenv("TT_DAYTONA_CREATE_RETRIES", "5")
    monkeypatch.setenv("TT_DAYTONA_HEARTBEAT_SEC", "0")
    sandbox = DaytonaSandbox(image="alpine:3.19")
    if len(errors) == 1:
        with pytest.raises(RuntimeError, match="BUILD_FAILED"):
            asyncio.run(sandbox.__aenter__())
    else:
        asyncio.run(sandbox.__aenter__())
        assert sandbox.sandbox_id == "sb"
    assert client.create.await_count == len(errors)
    assert [issue.kind for issue in sandbox.issue_tracker.issues] == expected_kinds
