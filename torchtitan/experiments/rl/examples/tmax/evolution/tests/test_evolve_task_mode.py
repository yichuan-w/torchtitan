# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""evolve_ondella.py --task: one task's pending signals in a process of its
own, locked per task, kept apart from a whole-root loop, folding under a lock
shared by the root's task processes, and finalizing only its own leftovers."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od
import finalize_interrupted_traces as fit
import pytest
import test_evolve_lineage as tl
from torchtitan.experiments.rl.examples.tmax import layout

SCRIPT = Path(od.__file__).resolve()
REPO = Path(__file__).resolve().parents[7]


@pytest.fixture(autouse=True)
def _no_publish_lock_left_behind(monkeypatch):
    monkeypatch.setattr(od, "_PUBLISH_LOCK", None)


def _run(root: layout.Root, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "TRL_BASE": str(root.path),
        "PYTHONPATH": os.pathsep.join(p for p in (str(REPO), os.environ.get("PYTHONPATH")) if p),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=300
    )


def test_task_processes_and_a_whole_root_loop_exclude_each_other(tmp_path):
    root = layout.Root(tmp_path / "root")
    fds = od.acquire_task_lock(root, "tw_a")
    try:
        assert od._PUBLISH_LOCK == root.evolution.path / "publish.lock"
        whole = _run(root, "--once")
        assert whole.returncode == 1 and "another instance already runs" in whole.stderr, whole.stderr
        same = _run(root, "--task", "tw_a")
        assert same.returncode == 1 and "evolution/locks (" in same.stderr, same.stderr
        # Another task of the same root runs (an empty root: nothing to handle).
        other = _run(root, "--task", "tw_b")
        assert other.returncode == 0, other.stdout + other.stderr
        assert "round done (task tw_b)" in root.evolution.loop_log.read_text()
    finally:
        for fd in fds:
            os.close(fd)
    fd = od.acquire_singleton(root.evolution.loop_lock)
    try:
        refused = _run(root, "--task", "tw_a")
        assert refused.returncode == 1 and "a whole-root loop runs" in refused.stderr, refused.stderr
    finally:
        os.close(fd)


def test_task_refuses_the_dev_modes(tmp_path):
    p = _run(layout.Root(tmp_path / "root"), "--task", "tw_a", "--dry")
    assert p.returncode == 1 and "--task takes none of" in p.stderr


def test_task_mode_handles_only_its_task(tmp_path, monkeypatch):
    root = tl._root(tmp_path, monkeypatch)
    tl._add_task(root, "tw_b")
    tl._signal(root, task="tw_a", group=7)
    other = tl._signal(root, task="tw_b", group=8)
    seen = tl._stub(monkeypatch)
    monkeypatch.setattr(od, "FREE_SLOT_POLL_SEC", 0.01)
    monkeypatch.setattr(od.logging, "basicConfig", lambda **kw: None)
    monkeypatch.setattr(od, "_snapshot_lineage", lambda root_, note: None)
    monkeypatch.setenv("EVOLVE_REWRITE_BUDGET_SEC", "86400")
    rounds = []
    real = od.run_round
    monkeypatch.setattr(od, "run_round", lambda *a, **kw: rounds.append(kw) or real(*a, **kw))
    monkeypatch.setattr(sys, "argv", ["evolve_ondella.py", "--task", "tw_a"])
    od.main()
    assert rounds[0]["only"] == "tw_a" and rounds[0]["workers"] == 1
    assert [s["signal"]["task"] for s in seen] == ["tw_a"]
    assert [(l["task"], l["outcome"]) for l in tl._ledger(root)] == [("tw_a", "handled")]
    assert other in {s.sid for s in od.discover(root, od.load_ledger(root))}
    assert root.evolution.status.exists()


def test_default_mode_keeps_the_root_lock_and_no_publish_lock(tmp_path):
    root = layout.Root(tmp_path / "root")
    p = _run(root, "--once")
    assert p.returncode == 0, p.stderr
    assert not (root.evolution.path / "locks").exists()
    assert od._PUBLISH_LOCK is None


def test_publish_lock_serializes_task_processes(tmp_path, monkeypatch):
    path = tmp_path / "publish.lock"
    monkeypatch.setattr(od, "_PUBLISH_LOCK", path)
    holder = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(holder, fcntl.LOCK_EX)  # another process's fold
    entered = threading.Event()

    def fold():
        with od._publish_lock():
            entered.set()

    t = threading.Thread(target=fold)
    t.start()
    assert not entered.wait(0.3), "entered while another fold held the lock"
    os.close(holder)
    assert entered.wait(10)
    t.join()


def _running(root: layout.Root, task: str) -> layout.RewriteDir:
    rw = root.evolution.task(task).rewrite("harder", "20261106-100000Z")
    layout.write_json_atomic(rw.meta, {"task": task, "status": "running", "finished": None,
                                       "signal": f"run/{task}--g1", "job": "harder"})
    return rw


def test_finalize_scoped_to_tasks(tmp_path):
    root = layout.Root(tmp_path / "root")
    rws = {t: _running(root, t) for t in ("tw_a", "tw_b", "tw_c")}
    assert fit.finalize_interrupted(root, tasks={"tw_a"})["marked"] == 1
    assert fit.finalize_interrupted(root, except_tasks={"tw_b"})["marked"] == 1
    status = {t: json.loads(rw.meta.read_text())["status"] for t, rw in rws.items()}
    assert status == {"tw_a": "interrupted", "tw_b": "running", "tw_c": "interrupted"}
