# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for `torchtitan.experiments.rl.eval_trace_recorder`."""

from __future__ import annotations

import json

from torchtitan.experiments.rl.eval_trace_recorder import ValidationTraceRecorder
from torchtitan.experiments.rl.rollout import (
    Rollout,
    RolloutGroup,
    RolloutStatus,
    RolloutTurn,
)
from torchtitan.experiments.rl.types import RolloutTurnID


def _decode(token_ids: list[int]) -> str:
    """Stand-in tokenizer: one token id per word."""
    vocab = {1: "<sys>", 2: "task", 3: "<think>", 4: "ls", 5: "<out>", 6: "done"}
    return " ".join(vocab.get(token_id, f"<{token_id}>") for token_id in token_ids)


def _turn(
    *,
    group_id: int,
    rollout_id: int,
    turn_id: int,
    prompt: list[int],
    completion: list[int],
) -> RolloutTurn:
    return RolloutTurn(
        rollout_id=RolloutTurnID(
            group_id=group_id, rollout_id=rollout_id, turn_id=turn_id
        ),
        prompt_token_ids=prompt,
        completion_token_ids=completion,
        completion_logprobs=[0.0] * len(completion),
        min_policy_version=1,
        prompt_messages=[{"role": "user", "content": "task"}],
        completion_message={"role": "assistant", "content": "ls"},
        env_messages=[{"role": "user", "content": "out"}],
    )


def _rollout(
    *,
    group_id: int,
    rollout_id: int,
    reward: float | None,
    diagnostics: dict | None = None,
) -> Rollout:
    # Turn 2's prompt extends turn 1's prompt+completion with the env output, which
    # is the token-in/token-out invariant the transcript rebuild relies on.
    return Rollout(
        group_id=group_id,
        rollout_id=rollout_id,
        status=RolloutStatus.COMPLETED,
        reward=reward,
        reward_breakdown={"tmax": reward},
        diagnostics=diagnostics or {},
        turns=[
            _turn(
                group_id=group_id,
                rollout_id=rollout_id,
                turn_id=0,
                prompt=[1, 2],
                completion=[3, 4],
            ),
            _turn(
                group_id=group_id,
                rollout_id=rollout_id,
                turn_id=1,
                prompt=[1, 2, 3, 4, 5],
                completion=[6],
            ),
        ],
    )


def _group(group_id: int, rewards: list[float | None]) -> RolloutGroup:
    return RolloutGroup(
        group_id=group_id,
        rollouts=[
            _rollout(group_id=group_id, rollout_id=idx, reward=reward)
            for idx, reward in enumerate(rewards)
        ],
    )


def _record(tmp_path, groups, task_ids, *, policy_version=20, **config_kwargs):
    recorder = ValidationTraceRecorder.Config(enable=True, **config_kwargs).build(
        dump_dir=str(tmp_path)
    )
    return recorder, recorder.record(
        policy_version=policy_version,
        groups=groups,
        task_ids=task_ids,
        decode=_decode,
    )


def test_disabled_recorder_writes_nothing(tmp_path):
    recorder = ValidationTraceRecorder.Config().build(dump_dir=str(tmp_path))
    assert not recorder.enabled
    assert (
        recorder.record(
            policy_version=0, groups=[_group(-1, [1.0])], task_ids=["t"], decode=_decode
        )
        is None
    )
    assert not (tmp_path / "validation_traces").exists()


def test_summary_reports_avg_and_pass_at_k(tmp_path):
    # 2 tasks x 4 trials: task-a solves 1/4, task-b solves 0/4.
    groups = [_group(-1, [1.0, 0.0, 0.0, 0.0]), _group(-2, [0.0, 0.0, 0.0, 0.0])]
    _, summary = _record(tmp_path, groups, ["task-a", "task-b"])

    assert summary.num_tasks == 2
    assert summary.num_trials == 8
    assert summary.num_pass == 1
    assert summary.avg_at_k == 0.125  # 1 / 8 trials
    assert summary.pass_at_k == 0.5  # 1 / 2 tasks


def test_infra_failure_invalidates_whole_score_and_keeps_raw_trials(tmp_path):
    groups = [_group(-1, [1.0, 0.0])]
    groups[0].rollouts[1].diagnostics["infra_failed"] = True
    _, summary = _record(tmp_path, groups, ["task-a"])
    assert not summary.valid
    assert summary.avg_at_k is None
    assert summary.pass_at_k is None
    root = tmp_path / "validation_traces" / "step-20"
    rows = json.loads((root / "index.json").read_text())
    assert [r["state"] for r in rows] == ["PASS", "INFRA_FAILED"]
    assert [r["reward"] for r in rows] == [1.0, 0.0]
    assert "rerun the full task set" in (root / "INDEX.md").read_text()


def test_missing_group_or_trial_invalidates_validation():
    from torchtitan.experiments.rl.eval_trace_recorder import validation_is_valid

    groups = [_group(-1, [1.0, 0.0])]
    assert validation_is_valid(groups, num_groups=1, group_size=2)
    assert not validation_is_valid(groups, num_groups=2, group_size=2)
    assert not validation_is_valid(groups, num_groups=1, group_size=3)
    groups[0].rollouts[1].reward = float("nan")
    assert not validation_is_valid(groups, num_groups=1, group_size=2)


def test_report_layout_and_index(tmp_path):
    _, summary = _record(tmp_path, [_group(-1, [1.0, 0.0])], ["adaptive-sampler"])
    step_dir = tmp_path / "validation_traces" / "step-20"

    index = json.loads((step_dir / "index.json").read_text())
    assert [row["state"] for row in index] == ["PASS", "FAIL"]
    assert index[0]["task"] == "adaptive-sampler"
    # Accumulated over both turns: prompts 2 + 5 ids, completions 2 + 1 ids.
    assert index[0]["prompt_tokens"] == 7
    assert index[0]["completion_tokens"] == 3
    assert index[0]["turns"] == 2

    assert json.loads((step_dir / "summary.json").read_text())["policy_version"] == 20

    index_md = (step_dir / "INDEX.md").read_text()
    assert "policy version 20" in index_md
    for row in index:
        assert row["path"] in index_md
        assert (step_dir / row["path"]).exists()


def test_transcript_is_sectioned_per_turn_without_repeating_the_prefix(tmp_path):
    """One section per turn, each showing only what that turn added.

    The fixture's turn 2 prompt is turn 1's prompt+completion plus the env output, so
    a reader must see the env delta alone under turn 2 -- not the shared prefix again.
    Emitting the whole session as one undivided block is what made a real 138 KB
    trace unreadable.
    """
    _, summary = _record(tmp_path, [_group(-1, [1.0])], ["task-a"])
    trace = (tmp_path / "validation_traces" / "step-20" / "traces" / "task-a").glob(
        "*.md"
    )
    text = next(trace).read_text()
    assert "| State | PASS |" in text
    assert "### Turn 1" in text and "### Turn 2" in text
    assert "**Task**" in text and "<sys> task" in text
    assert "**Assistant**" in text and "<think> ls" in text
    turn2 = text.split("### Turn 2", 1)[1]
    assert "**Environment**" in turn2 and "<out>" in turn2 and "done" in turn2
    assert "<sys>" not in turn2, "turn 2 must not repeat the shared prefix"
    assert "History rewritten" not in text


def test_transcript_keeps_both_sides_of_a_history_rewrite(tmp_path):
    """A rewritten history gets flagged, not silently dropped.

    Concatenating only the final prefix loses every turn before the rewrite, which is
    how a summarized Terminus-2 session produced a trace that appeared to begin
    mid-task with no indication why.
    """
    rollout = _rollout(group_id=-1, rollout_id=0, reward=1.0)
    # Turn 2's prompt no longer extends turn 1's: the history was rewritten.
    rollout.turns[1].prompt_token_ids = [5, 6]
    _record(tmp_path, [RolloutGroup(group_id=-1, rollouts=[rollout])], ["task-a"])
    text = next(
        (tmp_path / "validation_traces" / "step-20" / "traces" / "task-a").glob("*.md")
    ).read_text()
    assert "History rewritten before this turn" in text
    assert "<sys> task" in text, "the pre-rewrite turns must survive"
    assert "<out> done" in text, "the post-rewrite prompt must be shown in full"


def test_task_without_reward_invalidates_score(tmp_path):
    _, summary = _record(tmp_path, [_group(-1, [None, None])], ["task-a"])
    assert summary.num_pass == 0
    assert not summary.valid
    assert summary.avg_at_k is None
    assert summary.pass_at_k is None


def test_long_transcript_is_truncated(tmp_path):
    _, _ = _record(tmp_path, [_group(-1, [1.0])], ["task-a"], max_chars_per_trace=10)
    text = next(
        (tmp_path / "validation_traces" / "step-20" / "traces" / "task-a").glob("*.md")
    ).read_text()
    assert "characters omitted]" in text


def _diag_group(group_id: int, specs: list[tuple[float, str, int]]) -> RolloutGroup:
    """One group whose rollouts carry (reward, finish_reason, format_errors)."""
    return RolloutGroup(
        group_id=group_id,
        rollouts=[
            _rollout(
                group_id=group_id,
                rollout_id=idx,
                reward=reward,
                diagnostics={"finish_reason": finish, "format_errors": fmt},
            )
            for idx, (reward, finish, fmt) in enumerate(specs)
        ],
    )


def test_rollouter_diagnostics_become_index_columns(tmp_path):
    """finish_reason / format_errors must survive to the report: the group metrics
    average them away, so this is the only per-trial record of why a trial stopped."""
    groups = [
        _diag_group(
            -1,
            [
                (1.0, "submit", 0),
                (0.0, "stopped_early", 1),
                (0.0, "hit_max_turns", 0),
            ],
        )
    ]
    _record(tmp_path, groups, ["task-a"])
    step_dir = tmp_path / "validation_traces" / "step-20"

    index = json.loads((step_dir / "index.json").read_text())
    assert [row["finish_reason"] for row in index] == [
        "submit",
        "stopped_early",
        "hit_max_turns",
    ]
    assert [row["format_errors"] for row in index] == [0, 1, 0]

    index_md = (step_dir / "INDEX.md").read_text()
    # Columns are discovered from the rows, not hardcoded in the recorder.
    assert "| finish_reason | format_errors |" in index_md
    # Finish-reason breakdown above the table: the first thing to check when a
    # solve rate moves.
    assert "Trials by finish reason:" in index_md
    assert "`stopped_early`: 1 (33.3%)" in index_md

    trace = next((step_dir / "traces" / "task-a").glob("*_s1.md")).read_text()
    assert "| finish_reason | stopped_early |" in trace
    assert "| format_errors | 1 |" in trace


def test_report_works_without_any_diagnostics(tmp_path):
    """A rollouter that records no diagnostics still gets the base report."""
    _record(tmp_path, [_group(-1, [1.0, 0.0])], ["task-a"])
    index_md = (tmp_path / "validation_traces" / "step-20" / "INDEX.md").read_text()

    assert "Trials by finish reason:" not in index_md
    assert index_md.rstrip().endswith("|")
    assert "finish_reason" not in index_md
