# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_SWAP_BACK (#306 item 4): a whole group solving an easier revision
takes the task's evolving row out of the mix; its pinned original stays."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od
import lineage_policy as lp
import test_evolve_lineage as tl
from torchtitan.experiments.rl.examples.tmax import layout


def _root(tmp_path, monkeypatch, keep: str = "1"):
    root = tl._root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    monkeypatch.setenv("EVOLVE_KEEP_ORIGINAL", keep)
    monkeypatch.setenv("EVOLVE_SWAP_BACK", "1")
    tl._stub(monkeypatch)
    return root


def _step(root, group: int, rev: int, direction: str) -> dict:
    tl._signal(root, group=group, rev=rev, direction=direction)
    h = od.handle(root, od._replay_signal(root, f"{tl.RUN}/tw_a--g{group}"), declared={})
    od.fold(root, [h])
    return h


def _ids(root) -> list[str]:
    return [json.loads(ln)["metadata"]["instance_id"] for ln in root.mix.read_live().splitlines()]


def test_solved_easier_revision_swaps_back_and_pauses(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    _step(root, 7, 0, "easier")
    assert _ids(root) == ["tw_a", "tw_a~orig"]
    h = _step(root, 8, 1, "harder")
    assert (h["meta"]["job"], h["status"]) == ("swap_back", "accepted")
    assert _ids(root) == ["tw_a~orig"]
    lineage = layout.read_jsonl(root.evolution.task("tw_a").lineage)
    assert lp.swapped_back(lineage)
    assert lp.produced_by(lineage, 1) == "easier"

    tl._signal(root, group=9, rev=1, direction="easier")
    s = od._replay_signal(root, f"{tl.RUN}/tw_a--g9")
    assert od._defer_reason(root, s) == "paused: swapped back to the original"


def test_solved_harder_revision_is_hardened_again(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    _step(root, 7, 0, "harder")
    h = _step(root, 8, 1, "harder")
    assert h["meta"]["job"] == "harder"
    assert _ids(root) == ["tw_a", "tw_a~orig"]


def test_without_keep_original_the_easier_revision_is_hardened(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch, keep="0")
    _step(root, 7, 0, "easier")
    h = _step(root, 8, 1, "harder")
    assert (h["meta"]["job"], h["status"]) == ("harder", "accepted")
    assert _ids(root) == ["tw_a"]
