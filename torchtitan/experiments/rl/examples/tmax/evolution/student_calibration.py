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
    execution_failures = sum(
        row.get("sparse_reward", row.get("reward")) == 0
        and row.get("finish_reason")
        in {"hit_max_turns", "hit_time_budget", "hit_context_limit"}
        for row in scored
    )
    result = {
        "task": next(iter(tasks), None),
        "expected": expected,
        "attempts": len(rows),
        "scored": len(scored),
        "solved": solved,
        "turn_limit_failures": turn_failures,
        "execution_limit_failures": execution_failures,
        "target_min": 0.25,
        "target_max": 0.75,
    }
    if len(scored) != expected:
        action = "remeasure"
    elif execution_failures > expected / 4 or execution_failures > failures / 2:
        action = "review_execution"
    elif solved / expected > 0.75:
        action = "harder"
    elif solved / expected < 0.25:
        action = "easier"
    else:
        action = "confirm_independently"
    result["action"] = action
    return result


def assess_confirmations(batches: list[dict]) -> dict:
    """Confirm one frozen candidate under one evaluator with two disjoint seeds."""
    if len(batches) != 2:
        raise ValueError("two independent confirmation batches are required")
    for key in ("candidate_sha256", "evaluation_sha256"):
        identities = {batch[key] for batch in batches}
        if len(identities) != 1 or not next(iter(identities)):
            raise ValueError(f"confirmation must use the same {key}")
    seed_ranges = [set(range(batch["seed"], batch["seed"] + 16)) for batch in batches]
    if seed_ranges[0] & seed_ranges[1]:
        raise ValueError("confirmation sampling seeds overlap")
    for batch, expected_seeds in zip(batches, seed_ranges, strict=True):
        actual_seeds = [row.get("sampling_seed") for row in batch["attempts"]]
        if (
            any(type(seed) is not int for seed in actual_seeds)
            or len(actual_seeds) != len(set(actual_seeds))
            or not set(actual_seeds) <= expected_seeds
        ):
            raise ValueError(
                "confirmation requires distinct recorded sampling seeds in the declared range"
            )
    results = [assess_attempts(batch["attempts"], expected=16) for batch in batches]
    if len({result["task"] for result in results}) != 1:
        raise ValueError("confirmation attempts must refer to the same task")
    return {
        "action": "confirmed"
        if all(result["action"] == "confirm_independently" for result in results)
        else "not_confirmed",
        "batches": results,
        "candidate_sha256": batches[0]["candidate_sha256"],
        "evaluation_sha256": batches[0]["evaluation_sha256"],
        "seeds": [batch["seed"] for batch in batches],
    }
