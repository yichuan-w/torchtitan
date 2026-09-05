# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""One loop per root: the lock under evolution/loop.lock, held for the
process lifetime, with a heartbeat for nodes flock does not reach."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od
import pytest
from torchtitan.experiments.rl.examples.tmax import layout

SCRIPT = Path(od.__file__).resolve()
# The loop imports layout through the torchtitan package, so the child needs
# the repository root importable the way the loop's PYTHONPATH=$TRL_TT has it.
REPO = Path(__file__).resolve().parents[6]


def _env(root: layout.Root) -> dict[str, str]:
    # A fresh root per test so the lock, ledger and status never touch the
    # developer's or the live loop's.
    return {
        **os.environ,
        "TRL_BASE": str(root.path),
        "PYTHONPATH": os.pathsep.join(p for p in (str(REPO), os.environ.get("PYTHONPATH")) if p),
    }


def _once(root: layout.Root) -> subprocess.CompletedProcess:
    # --once over a root with no runs returns before touching the mix, so this
    # is the cheapest full trip through main(), lock included.
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--once"],
        env=_env(root),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_second_instance_is_refused_while_first_holds_the_lock(tmp_path) -> None:
    root = layout.Root(tmp_path / "root")
    lock = root.evolution.loop_lock
    fd = od.acquire_singleton(lock)
    try:
        assert f" pid={os.getpid()} " in lock.read_text()

        # flock is per open file, so the contender has to be another process.
        p = _once(root)

        assert p.returncode == 1, p.stdout + p.stderr
        assert "another instance already runs" in p.stderr
        assert f"pid={os.getpid()}" in p.stderr, p.stderr
        assert not root.evolution.loop_log.exists(), "refused instance wrote the log"
    finally:
        os.close(fd)


def test_lock_is_released_when_the_holder_exits(tmp_path) -> None:
    root = layout.Root(tmp_path / "root")
    first = _once(root)
    assert first.returncode == 0, first.stdout + first.stderr

    second = _once(root)

    assert second.returncode == 0, second.stdout + second.stderr
    assert root.evolution.loop_lock.exists()
    assert root.evolution.loop_log.exists()
    # An empty round still rebuilds status.json from what there is.
    assert root.evolution.status.exists()


def test_fresh_heartbeat_from_another_node_is_refused(tmp_path) -> None:
    lock = layout.Root(tmp_path / "root").evolution.loop_lock
    lock.parent.mkdir(parents=True)
    lock.write_text("host=some-other-node pid=1 started=now argv=x\n")

    with pytest.raises(SystemExit) as exc:
        od.acquire_singleton(lock)

    assert "different node" in str(exc.value)
    assert "some-other-node" in str(exc.value)


def test_stale_heartbeat_from_another_node_is_taken_over(tmp_path) -> None:
    lock = layout.Root(tmp_path / "root").evolution.loop_lock
    lock.parent.mkdir(parents=True)
    lock.write_text("host=some-other-node pid=1 started=long-ago argv=x\n")
    dead = time.time() - od.LOCK_STALE_SEC - 1
    os.utime(lock, (dead, dead))

    fd = od.acquire_singleton(lock)
    try:
        assert f" pid={os.getpid()} " in lock.read_text()
        assert "some-other-node" not in lock.read_text()
    finally:
        os.close(fd)
