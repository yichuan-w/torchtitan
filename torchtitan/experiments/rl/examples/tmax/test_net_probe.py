# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The boot-time network probe: what a rollout records about the sandbox's
reach, and that a probe that cannot run is recorded rather than raised."""

import asyncio

from torchtitan.experiments.rl.examples.tmax import rollouter


class _Sandbox:
    def __init__(self, stdout="", raise_=None):
        self.stdout, self.raise_ = stdout, raise_
        self.calls = []

    async def exec(self, cmd, *, user="root", timeout=120):
        self.calls.append((user, timeout))
        if self.raise_:
            raise self.raise_
        return 0, self.stdout, ""


def test_probe_parses_targets_and_icmp(monkeypatch):
    monkeypatch.delenv("TT_SANDBOX_NET_PROBE", raising=False)
    sb = _Sandbox(
        "pypi.org dns=ok dns_ms=12 http=200 http_ms=340\n"
        "github.com dns=ok dns_ms=9 http=err http_ms=5001\n"
        "icmp=fail\n"
    )
    out = asyncio.run(rollouter._net_probe(sb))
    assert out["targets"]["pypi.org"] == {"dns": "ok", "dns_ms": "12", "http": "200", "http_ms": "340"}
    assert out["targets"]["github.com"]["http"] == "err"
    assert out["icmp"] == "fail" and out["exit"] == 0
    assert sb.calls == [("root", rollouter._NET_PROBE_SEC)]


def test_probe_records_its_own_failure(monkeypatch):
    monkeypatch.delenv("TT_SANDBOX_NET_PROBE", raising=False)
    out = asyncio.run(rollouter._net_probe(_Sandbox(raise_=TimeoutError("exec 20s"))))
    assert out["error"].startswith("TimeoutError") and out["targets"] == {}


def test_probe_is_switchable(monkeypatch):
    monkeypatch.setenv("TT_SANDBOX_NET_PROBE", "0")
    sb = _Sandbox("pypi.org dns=ok dns_ms=1 http=200 http_ms=1\n")
    assert asyncio.run(rollouter._net_probe(sb)) == {}
    assert sb.calls == []
