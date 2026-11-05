# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lineage_policy as lp


def _group(rev, run, group, rewards, *, infra=False):
    return [
        {"rev": rev, "run": run, "group": group, "rollout": i, "reward": r,
         "infra_failed": infra}
        for i, r in enumerate(rewards)
    ]


def test_last_mixed_rev_is_the_highest_rev_with_a_mixed_group() -> None:
    index = (
        _group(0, "a", 1, [1, 0, 0])
        + _group(0, "a", 2, [1, 1, 0])
        + _group(1, "a", 3, [0, 0, 0])
        + _group(2, "b", 0, [1, 1, 1])
    )
    assert lp.last_mixed_rev(index) == {
        "rev": 0, "run": "a", "group": 2, "solved": 2, "total": 3,
    }
    index += _group(2, "b", 4, [0, 1, 0])
    assert lp.last_mixed_rev(index)["rev"] == 2


def test_validation_and_infra_failed_attempts_do_not_count() -> None:
    index = (
        _group(1, "a", -1, [1, 0])
        + _group(1, "a", 5, [0])
        + _group(1, "a", 5, [1], infra=True)
    )
    assert lp.last_mixed_rev(index) is None
    assert lp.training_groups(index) == [
        {"rev": 1, "run": "a", "group": 5, "solved": 0, "total": 1}
    ]


def test_no_attempts_has_no_mixed_rev() -> None:
    assert lp.last_mixed_rev([]) is None
