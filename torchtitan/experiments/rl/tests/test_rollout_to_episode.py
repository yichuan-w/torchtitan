# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for ``rollout_to_episode`` flattening (single- and multi-turn).

Verifies the per-token ``loss_mask`` masks env-injected tokens, that the flattened
sequence reconstructs the last turn's prompt+completion, that single-turn behavior
is unchanged (all-True mask), and that a broken cross-turn token prefix raises.
"""

import pytest

from torchtitan.experiments.rl.rollout import (
    Rollout,
    rollout_to_episode,
    RolloutStatus,
    RolloutTurn,
)


def _turn(prompt_token_ids, completion_token_ids, *, content="x"):
    return RolloutTurn(
        prompt_token_ids=prompt_token_ids,
        completion_token_ids=completion_token_ids,
        completion_logprobs=[-0.1 * (i + 1) for i in range(len(completion_token_ids))],
        completion_message={"role": "assistant", "content": content},
    )


def _rollout(turns):
    return Rollout(
        group_id="g",
        sample_id="s",
        status=RolloutStatus.COMPLETED,
        turns=turns,
        reward=1.0,
        advantage=0.5,
    )


def test_single_turn_all_trained():
    turn = _turn([1, 2, 3], [4, 5], content="hello")
    ep = rollout_to_episode(_rollout([turn]))

    assert ep.prompt_token_ids == [1, 2, 3]
    assert ep.completion_token_ids == [4, 5]
    assert ep.loss_mask == [True, True]
    assert ep.completion_logprobs == [-0.1, -0.2]
    assert ep.completion_text == "hello"
    assert ep.reward == 1.0
    assert ep.advantage == 0.5


def test_multi_turn_masks_env_tokens():
    # turn 0: prompt [1,2,3] -> completion [4,5]
    # turn 1: prompt [1,2,3,4,5,  6,7] (env injected [6,7]) -> completion [8]
    t0 = _turn([1, 2, 3], [4, 5])
    t1 = _turn([1, 2, 3, 4, 5, 6, 7], [8])
    ep = rollout_to_episode(_rollout([t0, t1]))

    # prompt is the first turn's prompt; everything after is flattened completion.
    assert ep.prompt_token_ids == [1, 2, 3]
    assert ep.completion_token_ids == [4, 5, 6, 7, 8]
    # env tokens [6,7] are masked out; assistant tokens [4,5] and [8] are trained.
    assert ep.loss_mask == [True, True, False, False, True]
    # env tokens get placeholder 0.0 logprobs.
    assert ep.completion_logprobs == [-0.1, -0.2, 0.0, 0.0, -0.1]
    # full sequence reconstructs the last turn's prompt + completion.
    assert (
        ep.prompt_token_ids + ep.completion_token_ids
        == t1.prompt_token_ids + t1.completion_token_ids
    )


def test_broken_prefix_raises():
    t0 = _turn([1, 2, 3], [4, 5])
    # turn 1's prompt does not extend turn 0's prompt+completion -> unsafe to flatten.
    t1 = _turn([9, 9, 9, 6, 7], [8])
    with pytest.raises(ValueError, match="not a continuation"):
        rollout_to_episode(_rollout([t0, t1]))


def test_no_turns_raises():
    with pytest.raises(ValueError, match="at least one turn"):
        rollout_to_episode(_rollout([]))
