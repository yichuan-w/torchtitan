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
