# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset, TMaxSample
    from torchtitan.experiments.rl.examples.tmax.env import TMaxEnv
    from torchtitan.experiments.rl.examples.tmax.rollouter import TMaxRollouter
    from torchtitan.experiments.rl.examples.tmax.rubric import RewardTMax


def __getattr__(name: str):
    # Layout and grading utilities run without loading datasets or model code.
    modules = {
        "TMaxDataset": "data",
        "TMaxSample": "data",
        "TMaxEnv": "env",
        "TMaxRollouter": "rollouter",
        "RewardTMax": "rubric",
    }
    if name not in modules:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{modules[name]}"), name)
    globals()[name] = value
    return value

__all__ = [
    "RewardTMax",
    "TMaxDataset",
    "TMaxEnv",
    "TMaxRollouter",
    "TMaxSample",
]
