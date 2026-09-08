# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Student difficulty decisions from complete, independently attributable trials."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from student_calibration import assess_attempts, assess_confirmations


def attempts(solved):
    return [
        dict(
            task="task",
            trial=str(i),
            sparse_reward=int(i < solved),
            infra_failed=False,
            finish_reason="submit",
        )
        for i in range(16)
    ]


@pytest.mark.parametrize(
    "solved,action",
    [
        (16, "harder"),
        (15, "harder"),
        (12, "confirm_independently"),
        (8, "confirm_independently"),
        (4, "confirm_independently"),
        (3, "easier"),
        (0, "easier"),
    ],
)
def test_complete_measurement(solved, action):
    assert assess_attempts(attempts(solved))["action"] == action


def test_missing_or_infrastructure_attempt_is_not_student_failure():
    rows = attempts(8)
    assert assess_attempts(rows[:-1])["action"] == "remeasure"
    rows[-1]["infra_failed"] = True
    result = assess_attempts(rows)
    assert result["scored"] == 15
    assert result["action"] == "remeasure"


@pytest.mark.parametrize(
    "reason", ["hit_max_turns", "hit_time_budget", "hit_context_limit"]
)
def test_execution_exhaustion_does_not_count_as_calibrated_difficulty(reason):
    rows = attempts(6)
    for row in rows[6:14]:
        row["finish_reason"] = reason
    result = assess_attempts(rows)
    assert result["action"] == "review_execution"
    assert result["execution_limit_failures"] == 8


@pytest.mark.parametrize("solved", [12, 13, 14, 15])
@pytest.mark.parametrize(
    "reason", ["hit_max_turns", "hit_time_budget", "hit_context_limit"]
)
def test_high_success_requires_hardening_despite_execution_failures(solved, reason):
    rows = attempts(solved)
    for row in rows[solved:]:
        row["finish_reason"] = reason
    result = assess_attempts(rows)
    assert result["action"] == ("harder" if solved > 12 else "review_execution")
    assert result["execution_limit_failures"] == 16 - solved


def test_no_duplicate_or_mixed_task_attempts():
    rows = attempts(8)
    rows[-1]["trial"] = rows[0]["trial"]
    with pytest.raises(ValueError, match="unique"):
        assess_attempts(rows)
    rows = attempts(8)
    rows[-1]["task"] = "other"
    with pytest.raises(ValueError, match="one task"):
        assess_attempts(rows)


def confirmations():
    return [
        dict(
            candidate_sha256="candidate",
            evaluation_sha256="evaluator",
            seed=seed,
            attempts=[
                {**row, "sampling_seed": seed + i}
                for i, row in enumerate(attempts(solved))
            ],
        )
        for seed, solved in ((10001, 9), (20001, 11))
    ]


def test_confirmation_requires_both_batches_in_range():
    batches = confirmations()
    assert assess_confirmations(batches)["action"] == "confirmed"
    for row in batches[1]["attempts"]:
        row["sparse_reward"] = 1
    assert assess_confirmations(batches)["action"] == "not_confirmed"


@pytest.mark.parametrize("key", ["candidate_sha256", "evaluation_sha256"])
def test_confirmation_cannot_mix_candidates_or_evaluators(key):
    batches = confirmations()
    batches[1][key] = "different"
    with pytest.raises(ValueError, match="same"):
        assess_confirmations(batches)


def test_confirmation_cannot_replay_overlapping_sample_seeds():
    batches = confirmations()
    batches[1]["seed"] = batches[0]["seed"] + 8
    with pytest.raises(ValueError, match="overlap"):
        assess_confirmations(batches)


def test_confirmation_cannot_use_different_tasks_from_the_same_dataset():
    batches = confirmations()
    for row in batches[1]["attempts"]:
        row["task"] = "other"
    with pytest.raises(ValueError, match="same task"):
        assess_confirmations(batches)


@pytest.mark.parametrize("recorded_seed", [None, 10001, 90001])
def test_confirmation_rejects_missing_repeated_or_unexpected_actual_seed(recorded_seed):
    batches = confirmations()
    batches[0]["attempts"][-1]["sampling_seed"] = recorded_seed
    with pytest.raises(ValueError, match="recorded sampling seeds"):
        assess_confirmations(batches)


def test_confirmation_with_distinct_seeds_still_requires_all_trials():
    batches = confirmations()
    batches[0]["attempts"].pop()
    result = assess_confirmations(batches)
    assert result["action"] == "not_confirmed"
    assert result["batches"][0]["action"] == "remeasure"
