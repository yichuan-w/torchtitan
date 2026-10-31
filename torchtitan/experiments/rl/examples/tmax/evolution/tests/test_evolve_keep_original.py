# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_KEEP_ORIGINAL: a task's first fold leaves a frozen copy of its
original row right after it, and a later fold does not add a second one."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od
import lineage_policy as lp
import test_evolve_lineage as tl


def _rows(root) -> list[dict]:
    return [json.loads(ln) for ln in root.mix.read_live().splitlines() if ln.strip()]


def _accepted(root, tid: str, group: int, rev: int = 0) -> dict:
    tl._signal(root, task=tid, group=group, rev=rev)
    h = od.handle(root, od._replay_signal(root, f"{tl.RUN}/{tid}--g{group}"), declared={})
    assert h["status"] == "accepted"
    return h


def _root(tmp_path, monkeypatch, keep: str):
    root = tl._root(tmp_path / keep, monkeypatch)
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    monkeypatch.setenv("EVOLVE_KEEP_ORIGINAL", keep)
    tl._add_task(root, "tw_1")
    tl._stub(monkeypatch)
    return root


def test_first_fold_pins_the_original_after_the_task(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch, "1")
    original = _rows(root)[0]
    od.fold(root, [_accepted(root, "tw_a", 7)])
    rows = _rows(root)
    assert [r["metadata"]["instance_id"] for r in rows] == ["tw_a", "tw_a~orig", "tw_1"]
    pinned = rows[1]
    assert pinned["metadata"].pop("evolve_frozen") is True
    assert pinned["label"] == "tw_a~orig"
    pinned["metadata"]["instance_id"] = pinned["label"] = "tw_a"
    assert pinned == original
    assert rows[0]["metadata"]["rev"] == 1

    od.fold(root, [_accepted(root, "tw_a", 8, rev=1)])
    rows = _rows(root)
    assert [r["metadata"]["instance_id"] for r in rows] == ["tw_a", "tw_a~orig", "tw_1"]
    assert (rows[0]["metadata"]["rev"], rows[1]["metadata"]["rev"]) == (2, 0)


def test_default_off_replaces_in_place(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch, "0")
    od.fold(root, [_accepted(root, "tw_a", 7)])
    assert [r["metadata"]["instance_id"] for r in _rows(root)] == ["tw_a", "tw_1"]


def test_frozen_original_renames_and_freezes() -> None:
    line = json.dumps({"label": "t", "metadata": {"instance_id": "t", "rev": 0}})
    iid, out = lp.frozen_original(line)
    assert iid == lp.original_id("t") == "t~orig"
    assert json.loads(out) == {
        "label": "t~orig",
        "metadata": {"instance_id": "t~orig", "rev": 0, "evolve_frozen": True},
    }
