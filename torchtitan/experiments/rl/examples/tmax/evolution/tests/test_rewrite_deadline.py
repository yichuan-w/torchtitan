# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rewrite_deadline import RewriteDeadline, RewriteDeadlineExceeded


def test_stage_timeout_reserves_final_validation() -> None:
    deadline = RewriteDeadline(
        started_at=100.0,
        expires_at=200.0,
        final_validation_reserve_sec=20,
    )

    assert deadline.timeout("author", 90, now=130.0) == 50
    assert (
        deadline.timeout(
            "final validation",
            90,
            reserve_final_validation=False,
            now=130.0,
        )
        == 70
    )


def test_stage_timeout_never_exceeds_its_own_limit() -> None:
    deadline = RewriteDeadline(
        started_at=100.0,
        expires_at=1000.0,
        final_validation_reserve_sec=20,
    )

    assert deadline.timeout("author", 60, now=130.0) == 60


def test_stage_refuses_to_consume_the_validation_reserve() -> None:
    deadline = RewriteDeadline(
        started_at=100.0,
        expires_at=200.0,
        final_validation_reserve_sec=20,
    )

    with pytest.raises(RewriteDeadlineExceeded, match="reserved") as raised:
        deadline.timeout("verifier repair", 90, now=180.0)

    assert raised.value.failure_type == "rewrite_deadline_exceeded"


def test_final_validation_uses_the_reserved_time() -> None:
    deadline = RewriteDeadline(
        started_at=100.0,
        expires_at=200.0,
        final_validation_reserve_sec=20,
    )

    assert deadline.timeout(
        "final validation", 90, reserve_final_validation=False, now=190.0
    ) == 10
    with pytest.raises(RewriteDeadlineExceeded):
        deadline.timeout(
            "final validation", 90, reserve_final_validation=False, now=200.0
        )
