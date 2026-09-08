# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The one cloud-side reaper that reaches a sandbox which never started.

``auto_stop_interval`` and ``auto_delete_interval`` are both defined on a
RUNNING sandbox going idle, so neither touches a BUILD_FAILED or ERROR one --
and those are what a stopped run leaves behind, one per create retry against a
task whose image does not build. ``ttl_minutes`` is wall-clock from creation
whatever the state; measured on a live account, a BUILD_FAILED sandbox created
with ``ttl_minutes=2`` was gone within 142s while an otherwise identical one
without it stayed.

It reaps live sandboxes on the same clock, hence off by default.
"""

from __future__ import annotations

import asyncio

import pytest

from torchtitan.experiments.rl.harness.sandbox import daytona as daytona_mod
from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox


class _Captured(Exception):
    """Ends __aenter__ once the create params exist -- nothing is provisioned."""


class _FakeClient:
    def __init__(self) -> None:
        self.params = None
        self.error: Exception | None = None

    async def create(self, params, timeout=None):
        self.params = params
        raise _Captured


def _run_aenter(monkeypatch, **env) -> _FakeClient:
    """Drive __aenter__ far enough to capture the params it would send.

    The fake create always raises, so nothing is provisioned; the params it was
    handed are the assertion target.
    """
    client = _FakeClient()

    async def _fake_get_shared_client(**_kwargs):
        return client

    monkeypatch.setattr(daytona_mod, "_get_shared_client", _fake_get_shared_client)
    for key in (
        "TT_DAYTONA_TTL_MIN",
        "TT_DAYTONA_EPHEMERAL",
        "TT_DAYTONA_AUTO_DELETE_MIN",
        "TT_DAYTONA_CREATE_RETRIES",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-key")
    # One attempt: the fake create always raises, and the retry loop would
    # otherwise sleep through its backoff before giving up.
    monkeypatch.setenv("TT_DAYTONA_CREATE_RETRIES", "0")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    sandbox = DaytonaSandbox(image="alpine:3.19")
    with pytest.raises(Exception) as excinfo:
        asyncio.run(sandbox.__aenter__())
    client.error = excinfo.value
    return client


def _create_params(monkeypatch, **env) -> object:
    client = _run_aenter(monkeypatch, **env)
    assert client.params is not None, "create was never reached"
    return client.params


def test_ttl_is_off_by_default(monkeypatch):
    # A TTL deletes running sandboxes at the same age it deletes orphans, so it
    # is never on unless someone chose a value against their rollout budget.
    params = _create_params(monkeypatch)
    assert params.ttl_minutes is None


@pytest.mark.parametrize("ephemeral", ["0", "1"])
def test_ttl_is_sent_on_both_create_paths(monkeypatch, ephemeral):
    # ttl_minutes has no conflict with ephemeral (unlike auto_delete_interval,
    # which the SDK forces to 0 there), so the knob must work in this region,
    # which accepts ephemeral creates only.
    params = _create_params(
        monkeypatch, TT_DAYTONA_TTL_MIN="240", TT_DAYTONA_EPHEMERAL=ephemeral
    )
    assert params.ttl_minutes == 240


def test_ephemeral_still_deletes_on_stop_sooner_than_any_ttl(monkeypatch):
    # Documents why raising TT_DAYTONA_AUTO_DELETE_MIN was never the fix: the
    # SDK pins auto_delete_interval to 0 under ephemeral -- delete immediately
    # on stop, sooner than any value we could pass -- and 0 still never fires
    # for a sandbox that has no running state to leave.
    params = _create_params(
        monkeypatch, TT_DAYTONA_EPHEMERAL="1", TT_DAYTONA_AUTO_DELETE_MIN="15"
    )
    assert params.auto_delete_interval == 0


def test_negative_ttl_is_rejected(monkeypatch):
    # The SDK reads a negative ttl as "disabled"; refuse it here instead, so a
    # typo cannot silently mean the opposite of what it looks like.
    client = _run_aenter(monkeypatch, TT_DAYTONA_TTL_MIN="-1")
    assert client.params is None, "create must not be reached with a bad TTL"
    assert isinstance(client.error, ValueError)
    assert "TT_DAYTONA_TTL_MIN" in str(client.error)
