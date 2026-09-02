# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The conversation a rollout dump carries, rebuilt from the adapter's deltas.

``rollout_samples.jsonl`` is the only record of what an episode looked like once
the sandbox is gone. The adapter stores each turn's messages as a delta -- the
whole history per turn is quadratic in episode length -- so this checks that
concatenating the laid-out fields gives back the conversation, and that nothing
is lost at the two edges: a dropped over-budget turn, and a mid-episode
re-render.
"""

from __future__ import annotations

from torchtitan.experiments.rl.examples.tmax.rollouter import _captured_to_turns
from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn


def _turn(
    *,
    messages: list,
    full: bool = False,
    completion: str | None = "ok",
    ids: list[int] | None = None,
) -> CapturedTurn:
    return CapturedTurn(
        prompt_token_ids=[1, 2, 3],
        completion_token_ids=ids if ids is not None else [4, 5],
        completion_logprobs=[0.0, 0.0] if ids is None else [0.0] * len(ids),
        min_policy_version=0,
        max_policy_version=0,
        finish_reason="stop",
        extends_previous=not full,
        messages=messages,
        messages_are_full_prompt=full,
        completion_message=(
            {"role": "assistant", "content": completion}
            if completion is not None
            else None
        ),
    )


def _flatten(turns: list) -> list:
    """The conversation as the reader reconstructs it: for each turn, whatever
    prompt it restated, then its completion, then the environment's reply."""
    out: list = []
    for t in turns:
        out.extend(t.prompt_messages)
        if t.completion_message is not None:
            out.append(t.completion_message)
        out.extend(t.env_messages)
    return out


def test_deltas_concatenate_back_into_the_conversation() -> None:
    opening = [
        {"role": "system", "content": "you are a terminal agent"},
        {"role": "user", "content": "make /tmp/x"},
    ]
    reply_1 = [{"role": "user", "content": "$ mkdir /tmp/x\n"}]
    reply_2 = [{"role": "user", "content": "$ ls /tmp\nx\n"}]
    captured = [
        _turn(messages=opening, full=True, completion="mkdir /tmp/x"),
        _turn(messages=reply_1, completion="ls /tmp"),
        _turn(messages=reply_2, completion="done"),
    ]

    turns = _captured_to_turns(captured, group_id="g", rollout_idx=0)

    assert [t.rollout_id.turn_id for t in turns] == [0, 1, 2]
    # Only the first turn restates a prompt; the rest are deltas, which is the
    # whole point -- turn 2 does not carry the opening again.
    assert turns[0].prompt_messages == opening
    assert turns[1].prompt_messages == []
    assert turns[2].prompt_messages == []
    # A turn's env reply is what arrived before the NEXT turn's prompt.
    assert turns[0].env_messages == reply_1
    assert turns[1].env_messages == reply_2
    assert turns[2].env_messages == []
    assert _flatten(turns) == [
        *opening,
        {"role": "assistant", "content": "mkdir /tmp/x"},
        *reply_1,
        {"role": "assistant", "content": "ls /tmp"},
        *reply_2,
        {"role": "assistant", "content": "done"},
    ]


def test_over_budget_turn_is_dropped_but_its_output_is_kept() -> None:
    """The last thing the agent saw is the output that blew the token budget.

    That turn has no completion, so it must not become a trainable RolloutTurn;
    dropping the observation along with it would hide why the episode ended.
    """
    last_output = [{"role": "user", "content": "a" * 100}]
    captured = [
        _turn(messages=[{"role": "user", "content": "go"}], full=True),
        _turn(messages=last_output, completion=None, ids=[]),
    ]

    turns = _captured_to_turns(captured, group_id="g", rollout_idx=0)

    assert len(turns) == 1
    assert turns[0].env_messages == last_output


def test_a_re_rendered_prompt_restates_the_history_it_branched_from() -> None:
    """A mid-episode re-render (compaction) breaks the delta chain.

    Its messages are the whole conversation as rewritten, not an increment, so
    they belong in that turn's prompt_messages -- concatenating them onto the
    previous turn's env reply would claim the environment said them.
    """
    rewritten = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "step one"},
        {"role": "user", "content": "[history compacted]"},
    ]
    captured = [
        _turn(messages=[{"role": "user", "content": "go"}], full=True),
        _turn(messages=rewritten, full=True, completion="step two"),
    ]

    turns = _captured_to_turns(captured, group_id="g", rollout_idx=0)

    assert turns[0].env_messages == []
    assert turns[1].prompt_messages == rewritten
