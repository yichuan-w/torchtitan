# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Script-drift metrics over the policy's completions (rollouter._script_mix_metrics)."""

from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl.examples.tmax.rollouter import _script_mix_metrics
from torchtitan.experiments.rl.observability.metrics import Mean
from torchtitan.experiments.rl.rollout.types import Rollout, RolloutStatus


def _turn(thinking: str, text: str):
    return SimpleNamespace(
        completion_message={
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": thinking},
                {"type": "text", "text": text},
            ],
        }
    )


def _rollout(i, turns):
    return Rollout(
        group_id=0,
        rollout_id=i,
        status=RolloutStatus.COMPLETED,
        reward=0.0,
        turns=turns,
    )


def _values(rollouts):
    values = {}
    for metric in _script_mix_metrics(rollouts):
        assert isinstance(metric.value, Mean)
        values[metric.key] = metric.value.value / metric.value.count
    return values


def test_clean_english_scores_zero():
    values = _values([_rollout(0, [_turn("Look at the file.", "ls -la")])])
    assert values == {
        "rollout/nonlatin_per_1k_chars_mean": 0.0,
        "rollout/nonlatin_turn_frac": 0.0,
        "rollout/latin_cjk_adjacent_turn_frac": 0.0,
    }


def test_in_word_cjk_counts_as_adjacent_and_spaced_chinese_does_not():
    drifted = _turn("min(1, f(x) / exp(upper恩velope(x)))", "")
    chinese = _turn("任务 说明", "")
    clean = _turn("plain", "")
    values = _values(
        [_rollout(0, [drifted, clean]), _rollout(1, [chinese, clean])]
    )
    assert values["rollout/nonlatin_turn_frac"] == pytest.approx(0.5)
    assert values["rollout/latin_cjk_adjacent_turn_frac"] == pytest.approx(0.25)
    assert values["rollout/nonlatin_per_1k_chars_mean"] > 0


def test_other_scripts_and_density():
    arabic = SimpleNamespace(
        completion_message={"role": "assistant", "content": "abcdefghi" + "ظ"}
    )  # 1 non-Latin char in 10
    values = _values([_rollout(0, [arabic])])
    assert values["rollout/nonlatin_per_1k_chars_mean"] == pytest.approx(100.0)
    assert values["rollout/latin_cjk_adjacent_turn_frac"] == 0.0


def test_siblings_without_completions_are_skipped():
    assert _script_mix_metrics([_rollout(0, []), _rollout(1, [object()])]) == []
    values = _values([_rollout(0, []), _rollout(1, [_turn("", "任务")])])
    assert values["rollout/nonlatin_turn_frac"] == 1.0
