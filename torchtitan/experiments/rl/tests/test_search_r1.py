# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for the Search-R1 example: env action parsing + EM/format rubric.

No GPU, no retrieval server: the retrieval call is monkeypatched.
"""

import asyncio

from torchtitan.experiments.rl.examples.search_r1 import (
    env as sr1_env,
    RewardAnswerEM,
    RewardFormat,
    SearchR1Env,
    SearchR1Example,
)
from torchtitan.experiments.rl.rollout import Rollout, RolloutStatus, RolloutTurn


def _build_env(monkeypatch, question="Who wrote Hamlet?"):
    async def _fake_search(query, *, url, topk, timeout_s=60.0):
        return f"Doc 1(Title: T) results for {query}"

    monkeypatch.setattr(sr1_env, "search", _fake_search)
    return SearchR1Env(
        SearchR1Env.Config(),
        env_input=SearchR1Example(question=question, golden_answers=["Shakespeare"]),
    )


def test_env_search_action_injects_information(monkeypatch):
    env = _build_env(monkeypatch)
    out = asyncio.run(
        env.step(
            {
                "role": "assistant",
                "content": "<think>hm</think><search>Hamlet author</search>",
            }
        )
    )
    assert out.done is False
    assert len(out.env_messages) == 1
    content = out.env_messages[0]["content"]
    assert "<information>" in content and "</information>" in content
    assert "Hamlet author" in content


def test_env_answer_action_terminates(monkeypatch):
    env = _build_env(monkeypatch)
    out = asyncio.run(
        env.step({"role": "assistant", "content": "<answer>Shakespeare</answer>"})
    )
    assert out.done is True
    assert out.env_messages == []


def test_env_invalid_action_nudges(monkeypatch):
    env = _build_env(monkeypatch)
    out = asyncio.run(env.step({"role": "assistant", "content": "no tags here"}))
    assert out.done is False
    assert "invalid" in out.env_messages[0]["content"].lower()


def _answer_rollout(text: str) -> Rollout:
    return Rollout(
        group_id="g",
        sample_id="s",
        status=RolloutStatus.COMPLETED,
        turns=[
            RolloutTurn(
                prompt_token_ids=[1],
                completion_token_ids=[2],
                completion_logprobs=[-0.1],
                completion_message={"role": "assistant", "content": text},
            )
        ],
    )


def test_reward_em_exact_match_normalized():
    em = RewardAnswerEM(RewardAnswerEM.Config())
    ex = SearchR1Example(question="q", golden_answers=["Shakespeare"])
    # normalization lowercases + strips punctuation/articles.
    r = asyncio.run(em(_answer_rollout("<answer>The Shakespeare.</answer>"), ex))
    assert r == 1.0


def test_reward_em_mismatch():
    em = RewardAnswerEM(RewardAnswerEM.Config())
    ex = SearchR1Example(question="q", golden_answers=["Shakespeare"])
    r = asyncio.run(em(_answer_rollout("<answer>Marlowe</answer>"), ex))
    assert r == 0.0


def test_reward_em_uses_last_answer():
    em = RewardAnswerEM(RewardAnswerEM.Config())
    ex = SearchR1Example(question="q", golden_answers=["Paris"])
    r = asyncio.run(
        em(_answer_rollout("<answer>London</answer> ... <answer>Paris</answer>"), ex)
    )
    assert r == 1.0


def test_reward_format():
    fmt = RewardFormat(RewardFormat.Config())
    ex = SearchR1Example(question="q", golden_answers=["x"])
    assert asyncio.run(fmt(_answer_rollout("<answer>x</answer>"), ex)) == 1.0
    assert asyncio.run(fmt(_answer_rollout("no answer block"), ex)) == 0.0
