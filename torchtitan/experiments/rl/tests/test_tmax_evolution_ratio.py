# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import json
from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl.examples.tmax import rollouter
from torchtitan.experiments.rl.rollout.types import Rollout, RolloutStatus


@pytest.mark.parametrize(
    "ratio,rewards,direction,solved,total",
    [
        (0.9, [1.0] * 14 + [0.0] * 2, None, 14, 16),
        (0.9, [0.0] + [1.0] * 15, "harder", 15, 16),
        (0.9, [1.0] * 16, "harder", 16, 16),
        (1.0, [1.0] * 15 + [0.0], None, 15, 16),
        (1.0, [1.0] * 16, "harder", 16, 16),
        (0.9, [1.0] * 9 + [0.0], "harder", 9, 10),
        (0.9, [1.0] * 9 + [0.0, float("nan"), None], "harder", 9, 10),
        (0.9, [0.0] * 16, "easier", 0, 16),
        (0.9, [1.0, float("nan")], None, 1, 1),
        (0.9, [], None, 0, 0),
    ],
)
def test_evolution_ratio(
    monkeypatch, tmp_path, ratio, rewards, direction, solved, total
):
    monkeypatch.setenv("TRL_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("SWE_EVOLUTION_SIGNALS", "1")
    worker = object.__new__(rollouter.TMaxRollouter)
    worker._reward_mode = "sparse"
    worker._evolution_harder_ratio = ratio
    worker._evolution_easier_ratio = 0.0
    sample = SimpleNamespace(instance_id="task", rev=0, image="image")
    siblings = [
        Rollout(
            group_id=3,
            rollout_id=i,
            status=RolloutStatus.COMPLETED,
            reward=reward,
            turns=[object()],
            advantage=float(i),
            diagnostics={"record": f"attempt-{i}.jsonl"},
        )
        for i, reward in enumerate(rewards)
    ]
    worker._maybe_emit_evolution_signal(sample, siblings)
    signals = list(tmp_path.glob("signals/*.json"))
    assert len(signals) == int(direction is not None)
    assert [r.advantage for r in siblings] == list(map(float, range(len(rewards))))
    if signals:
        signal = json.loads(signals[0].read_text())
        assert (signal["direction"], signal["solved"], signal["total"]) == (
            direction,
            solved,
            total,
        )
        assert len(signal["attempts"]) == total


@pytest.mark.parametrize("ratio", [0, -0.1, 1.01, float("nan"), float("inf")])
def test_invalid_ratio_fails_before_dataset_loading(ratio):
    with pytest.raises(ValueError, match="evolution_harder_ratio"):
        rollouter.TMaxRollouter(
            rollouter.TMaxRollouter.Config(evolution_harder_ratio=ratio)
        )


def test_default_ratio_requires_all_pass():
    assert rollouter.TMaxRollouter.Config().evolution_harder_ratio == 1.0


def test_default_easier_ratio_requires_no_solve():
    assert rollouter.TMaxRollouter.Config().evolution_easier_ratio == 0.0


@pytest.mark.parametrize("ratio", [-0.1, 1.0, 1.01, float("nan")])
def test_invalid_easier_ratio_fails_before_dataset_loading(ratio):
    with pytest.raises(ValueError, match="evolution_easier_ratio"):
        rollouter.TMaxRollouter(
            rollouter.TMaxRollouter.Config(evolution_easier_ratio=ratio)
        )


def test_easier_ratio_must_stay_below_harder_ratio():
    with pytest.raises(ValueError, match="evolution_easier_ratio"):
        rollouter.TMaxRollouter(
            rollouter.TMaxRollouter.Config(
                evolution_harder_ratio=0.5, evolution_easier_ratio=0.5
            )
        )


# A failure is not always scored 0: SWE_WRONG_SUBMIT_PENALTY (0.3 in our runs)
# makes a graded-wrong submit negative. A group that solved nothing asks for
# `easier` whatever its failures scored, and one wrong submit used to be enough
# to emit nothing at all.
@pytest.mark.parametrize(
    "easier_ratio,rewards,direction,solved,total",
    [
        (0.0, [0.0] * 15 + [-0.3], "easier", 0, 16),
        (0.0, [-0.3] * 16, "easier", 0, 16),
        (0.0, [0.0] * 14 + [-0.3, 1.0], None, 1, 16),
        (0.125, [0.0] * 14 + [-0.3, 1.0], "easier", 1, 16),
        # The ratio is inclusive, so a fraction exactly on it still asks.
        (0.125, [0.0] * 13 + [-0.3, 1.0, 1.0], "easier", 2, 16),
        (0.125, [0.0] * 12 + [-0.3, 1.0, 1.0, 1.0], None, 3, 16),
    ],
)
def test_easier_ratio_counts_solves_not_zero_rewards(
    monkeypatch, tmp_path, easier_ratio, rewards, direction, solved, total
):
    monkeypatch.setenv("TRL_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("SWE_EVOLUTION_SIGNALS", "1")
    worker = object.__new__(rollouter.TMaxRollouter)
    worker._reward_mode = "sparse"
    worker._evolution_harder_ratio = 1.0
    worker._evolution_easier_ratio = easier_ratio
    siblings = [
        Rollout(
            group_id=4,
            rollout_id=i,
            status=RolloutStatus.COMPLETED,
            reward=reward,
            turns=[object()],
            diagnostics={"record": f"attempt-{i}.jsonl"},
        )
        for i, reward in enumerate(rewards)
    ]
    worker._maybe_emit_evolution_signal(
        SimpleNamespace(instance_id="task", rev=0, image="image"), siblings
    )
    signals = list(tmp_path.glob("signals/*.json"))
    assert len(signals) == int(direction is not None)
    if signals:
        signal = json.loads(signals[0].read_text())
        assert (signal["direction"], signal["solved"], signal["total"]) == (
            direction,
            solved,
            total,
        )


@pytest.mark.parametrize(
    "rewards,expected", [([0.5, 0.5], "harder"), ([0.5, 1.0], None)]
)
def test_dense_evolution_keeps_zero_variance_rule(
    monkeypatch, tmp_path, rewards, expected
):
    monkeypatch.setenv("TRL_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("SWE_EVOLUTION_SIGNALS", "1")
    worker = object.__new__(rollouter.TMaxRollouter)
    worker._reward_mode = "dense"
    worker._evolution_harder_ratio = 0.9
    worker._evolution_easier_ratio = 0.0
    siblings = [
        Rollout(group_id=1, rollout_id=i, status=RolloutStatus.COMPLETED, reward=r)
        for i, r in enumerate(rewards)
    ]
    worker._maybe_emit_evolution_signal(
        SimpleNamespace(instance_id="task", rev=0), siblings
    )
    signals = list(tmp_path.glob("signals/*.json"))
    assert len(signals) == int(expected is not None)
    if signals:
        assert json.loads(signals[0].read_text())["direction"] == expected
