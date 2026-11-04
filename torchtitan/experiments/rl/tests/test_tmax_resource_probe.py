# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import asyncio

from torchtitan.experiments.rl.examples.tmax.rollouter import _resource_probe


class _Sandbox:
    def __init__(self, result):
        self.result = result

    async def exec(self, script, *, user, timeout):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_resource_probe_parses_byte_counts_and_skips_junk() -> None:
    stdout = (
        "disk_used=1074438144\ndisk_total=6442450944\n"
        "cg_memory.peak=1850298368\ncg_memory.max=max\ncg_oom_kill=0\n"
    )
    assert asyncio.run(_resource_probe(_Sandbox((0, stdout, "")))) == {
        "disk_used": 1074438144,
        "disk_total": 6442450944,
        "cg_memory.peak": 1850298368,
        "cg_oom_kill": 0,
    }


def test_resource_probe_never_raises() -> None:
    out = asyncio.run(_resource_probe(_Sandbox(RuntimeError("sandbox gone"))))
    assert out == {"error": "RuntimeError: sandbox gone"}


def test_resource_probe_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setenv("TT_SANDBOX_RESOURCE_PROBE", "0")
    assert asyncio.run(_resource_probe(_Sandbox((0, "disk_used=1\n", "")))) == {}
