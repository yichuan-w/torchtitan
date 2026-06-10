# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from dataclasses import dataclass, field

from torchtitan.experiments.rl.environment import TokenEnv
from torchtitan.experiments.rl.examples.search_r1.data import SearchR1Dataset
from torchtitan.experiments.rl.examples.search_r1.env import SearchR1Env
from torchtitan.experiments.rl.examples.search_r1.rubric import (
    RewardAnswerEM,
    RewardFormat,
)

from torchtitan.experiments.rl.rollout.rollouter import Rollouter

from torchtitan.experiments.rl.rubrics import Rubric


# Default Search-R1 NQ/HotpotQA parquet locations (prepared via Search-R1's
# data_process scripts). Override `train_dataset`/`validation_dataset` data_path
# in a config to point elsewhere. See README.md for data + retriever setup.
DEFAULT_TRAIN_PARQUET = "/home/yichuan/Search-R1/data/nq_hotpotqa_train/train.parquet"
DEFAULT_TEST_PARQUET = "/home/yichuan/Search-R1/data/nq_hotpotqa_train/test.parquet"


class SearchR1Rollouter(Rollouter):
    """The Search-R1 rollouter: NQ/HotpotQA train/val datasets, a multi-turn
    search env, and an EM + format rubric. Pure config — all behavior
    (`make_env_group`, `sample_*`, `score_group`) is inherited from `Rollouter`.

    `token_env.max_num_turns` bounds the search/answer turns per rollout.
    """

    @dataclass(kw_only=True, slots=True)
    class Config(Rollouter.Config):
        train_dataset: SearchR1Dataset.Config = field(
            default_factory=lambda: SearchR1Dataset.Config(
                data_path=DEFAULT_TRAIN_PARQUET, seed=42
            )
        )
        validation_dataset: SearchR1Dataset.Config = field(
            default_factory=lambda: SearchR1Dataset.Config(
                # Evaluate on the NQ test split only (in-distribution with the
                # NQ+HotpotQA train set), like slime's per-benchmark eval.
                data_path=DEFAULT_TEST_PARQUET,
                seed=99,
                data_source="nq",
            )
        )
        rubric: Rubric.Config = field(
            default_factory=lambda: Rubric.Config(
                reward_fns=[
                    RewardAnswerEM.Config(weight=1.0),
                    RewardFormat.Config(weight=0.1),
                ],
                # No <answer> on a truncated rollout -> no reward, no learning signal.
                truncation_reward=0.0,
            )
        )
        message_env: SearchR1Env.Config = field(default_factory=SearchR1Env.Config)
        token_env: TokenEnv.Config = field(
            default_factory=lambda: TokenEnv.Config(
                max_num_turns=4, max_rollout_tokens=3072
            )
        )
