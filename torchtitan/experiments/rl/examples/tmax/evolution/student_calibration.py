# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Decide the next calibration action from one task's measured attempts."""

from __future__ import annotations

import math


def assess_attempts(rows: list[dict], *, expected: int = 16) -> dict:
    """Keep incomplete measurements and execution burden out of acceptance."""
    if expected < 1:
        raise ValueError("expected must be positive")
    tasks = {row["task"] for row in rows}
    trials = [row["trial"] for row in rows]
    if len(tasks) > 1 or len(trials) != len(set(trials)):
        raise ValueError("one task with unique trials is required")
    if len(rows) > expected:
        raise ValueError("more attempts than the declared measurement")
    scored = []
    for row in rows:
        reward = row.get("sparse_reward", row.get("reward"))
        if row.get("infra_failed") or reward is None:
            continue
        if not math.isfinite(reward) or reward not in (0, 1):
            raise ValueError("calibration requires binary finite rewards")
        scored.append(row)
    solved = sum(row.get("sparse_reward", row.get("reward")) == 1 for row in scored)
    failures = len(scored) - solved
    turn_failures = sum(
        row.get("sparse_reward", row.get("reward")) == 0
        and row.get("finish_reason") == "hit_max_turns"
        for row in scored
    )
    result = {
        "task": next(iter(tasks), None),
        "expected": expected,
        "attempts": len(rows),
        "scored": len(scored),
        "solved": solved,
        "turn_limit_failures": turn_failures,
        "target_min": 0.25,
        "target_max": 0.75,
    }
    if len(scored) != expected:
        action = "remeasure"
    elif turn_failures > expected / 4 or turn_failures > failures / 2:
        action = "review_execution"
    elif solved / expected > 0.75:
        action = "harder"
    elif solved / expected < 0.25:
        action = "easier"
    else:
        action = "confirm_independently"
    result["action"] = action
    return result
