# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""One wall-clock deadline shared by every stage of a task rewrite."""

from __future__ import annotations

import time
from dataclasses import dataclass


class RewriteDeadlineExceeded(RuntimeError):
    """A rewrite has no time left for a stage under its shared deadline."""

    failure_type = "rewrite_deadline_exceeded"


@dataclass(frozen=True)
class RewriteDeadline:
    started_at: float
    expires_at: float
    final_validation_reserve_sec: int

    def __post_init__(self) -> None:
        if self.expires_at <= self.started_at:
            raise ValueError("rewrite deadline must be after its start time")
        if self.final_validation_reserve_sec < 0:
            raise ValueError("final validation reserve must be nonnegative")

    @property
    def budget_sec(self) -> float:
        return self.expires_at - self.started_at

    def remaining_sec(self, *, now: float | None = None) -> float:
        return self.expires_at - (time.time() if now is None else now)

    def timeout(
        self,
        stage: str,
        maximum_sec: int,
        *,
        reserve_final_validation: bool = True,
        now: float | None = None,
    ) -> int:
        """Return a stage timeout bounded by the shared rewrite deadline."""
        if maximum_sec <= 0:
            raise ValueError("stage timeout must be positive")
        remaining = self.remaining_sec(now=now)
        reserve = self.final_validation_reserve_sec if reserve_final_validation else 0
        available = remaining - reserve
        if available < 1:
            raise RewriteDeadlineExceeded(
                f"rewrite deadline leaves {max(0.0, remaining):.0f}s for {stage}; "
                f"{reserve}s is reserved for final validation "
                f"(total budget {self.budget_sec:.0f}s)"
            )
        return min(maximum_sec, int(available))
