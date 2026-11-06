# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_RETIRE_AFTER_MISSES (#306 item 3): after N revisions in a row miss on
their first group, the task goes back to its last mixed revision as a new
revision, and is paused from then on."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od
import lineage_policy as lp
import test_evolve_lineage as tl
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record


def _g(rev, group, rewards, run="a"):
    return [
        {"rev": rev, "run": run, "group": group, "rollout": i, "reward": r, "infra_failed": False}
        for i, r in enumerate(rewards)
    ]


def test_first_group_misses_counts_back_from_the_current_rev() -> None:
    index = _g(0, 1, [1, 0]) + _g(1, 2, [1, 1]) + _g(2, 3, [0, 0]) + _g(2, 4, [1, 0]) + _g(3, 5, [0, 0])
    assert lp.first_group_misses(index, 3) == 3
    index = _g(1, 2, [1, 0]) + _g(2, 3, [0, 0]) + _g(3, 4, [1, 1])
    assert lp.first_group_misses(index, 3) == 2
    assert lp.first_group_misses(index, 4) == 0  # no group on r4 yet


def test_revert_target_is_the_last_earlier_mixed_rev_or_the_original() -> None:
    misses = _g(2, 3, [0, 0]) + _g(3, 4, [1, 1]) + _g(4, 5, [0, 0])
    assert lp.revert_target(_g(1, 2, [1, 0]) + misses, 4, 3) == 1
    assert lp.revert_target(misses, 4, 3) == 0
    assert lp.revert_target(misses, 4, 4) is None
    assert lp.revert_target(misses, 4, 0) is None
    # The current revision has produced a mixed group since its first: keep it.
    assert lp.revert_target(misses + _g(4, 6, [1, 0]), 4, 3) is None


def test_reverted_at_reads_the_fold_line() -> None:
    lineage = [{"event": "fold", "to_rev": 3}, {"event": "fold", "to_rev": 4, "reverted_to": 1}]
    assert lp.reverted_at(lineage, 4) == 1
    assert lp.reverted_at(lineage, 3) is None


def _step(root, group: int, rev: int) -> dict:
    tl._signal(root, group=group, rev=rev)
    h = od.handle(root, od._replay_signal(root, f"{tl.RUN}/tw_a--g{group}"), declared={})
    assert h["status"] == "accepted"
    od.fold(root, [h])
    return h


def _mixed_group(root, group: int, rev: int) -> None:
    r = root.run(tl.RUN)
    for i, reward in enumerate([1.0, 0.0]):
        rollout_record.write_record(
            r.rollout_record("tw_a", group, i),
            {"task": "tw_a", "rev": rev, "run": tl.RUN, "group": group, "rollout": i, "reward": reward},
            [{"turn": 1, "keystrokes": ["ls\n"], "output": ""}],
        )


@pytest.mark.parametrize("mixed_rev", [None, 1])
def test_three_misses_revert_and_pause(tmp_path, monkeypatch, mixed_rev):
    root = tl._root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    monkeypatch.setenv("EVOLVE_RETIRE_AFTER_MISSES", "3")
    tl._stub(monkeypatch)
    task = root.evolution.task("tw_a")
    _step(root, 7, 0)  # r1
    if mixed_rev == 1:
        _mixed_group(root, 6, 1)  # r1's first group was mixed
    _step(root, 8, 1)  # r2
    _step(root, 9, 2)  # r3
    if mixed_rev is None:
        current, target = 3, 0  # r1..r3 each missed on their first group
    else:
        # r3 and r2 missed, but r1's first group was mixed: an ordinary rewrite.
        assert _step(root, 10, 3)["meta"]["job"] == "harder"
        current, target = 4, 1
    h = _step(root, 20, current)
    assert h["meta"]["job"] == "revert"
    assert h["meta"]["reverted_to"] == target
    new = current + 1
    assert task.latest_rev() == new
    assert (task.rev(new) / "instruction.md").read_text() == (
        task.rev(target) / "instruction.md"
    ).read_text()
    row = json.loads(root.mix.read_live().splitlines()[0])
    assert row["metadata"]["rev"] == new
    assert lp.reverted_at(layout.read_jsonl(task.lineage), new) == target

    tl._signal(root, group=30, rev=new)
    s = od._replay_signal(root, f"{tl.RUN}/tw_a--g30")
    assert od._defer_reason(root, s) == f"paused: r{new} is the retire copy of r{target}"
