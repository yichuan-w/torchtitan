# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import re
import string
from dataclasses import dataclass

from torchtitan.experiments.rl.examples.search_r1.data import SearchR1Example
from torchtitan.experiments.rl.rollout import Rollout
from torchtitan.experiments.rl.rubrics import RewardFn


_ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL)


def _normalize_answer(s: str) -> str:
    """Lowercase, strip punctuation/articles, and collapse whitespace.

    Ported from Search-R1's ``qa_em_format.normalize_answer`` so EM scoring matches.
    """
    s = s.lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def _all_assistant_text(rollout: Rollout) -> str:
    """Concatenate every assistant completion's text across the rollout's turns."""
    texts: list[str] = []
    for turn in rollout.turns:
        content = (turn.completion_message or {}).get("content")
        if isinstance(content, str):
            texts.append(content)
    return "\n".join(texts)


def _extract_answer(rollout: Rollout) -> str | None:
    """Return the last ``<answer>...</answer>`` content in the rollout, or None."""
    matches = _ANSWER_RE.findall(_all_assistant_text(rollout))
    return matches[-1].strip() if matches else None


class RewardAnswerEM(RewardFn):
    """`1.0` if the final `<answer>` exactly matches (normalized) any gold answer."""

    @dataclass(kw_only=True, slots=True)
    class Config(RewardFn.Config):
        pass

    async def __call__(self, rollout: Rollout, env_input: SearchR1Example) -> float:
        answer = _extract_answer(rollout)
        if answer is None:
            return 0.0
        normalized = _normalize_answer(answer)
        return (
            1.0
            if any(normalized == _normalize_answer(g) for g in env_input.golden_answers)
            else 0.0
        )


class RewardFormat(RewardFn):
    """`1.0` if the rollout produced a well-formed `<answer>...</answer>` block."""

    @dataclass(kw_only=True, slots=True)
    class Config(RewardFn.Config):
        pass

    async def __call__(self, rollout: Rollout, env_input: object) -> float:
        return 1.0 if _extract_answer(rollout) is not None else 0.0


__all__ = ["RewardAnswerEM", "RewardFormat"]
