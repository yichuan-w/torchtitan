# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from importlib import import_module

import pytest


@pytest.mark.parametrize("package,source,names", [
    ("", "models.vllm_registry", ["register_to_vllm"]),
    ("", "models.vllm_wrapper", ["VLLMModelWrapper"]),
    ("harness", "harness.adapters", ["AnthropicAdapter", "CapturedTurn"]),
    ("examples.tmax", "examples.tmax.data", ["TMaxDataset", "TMaxSample"]),
    ("examples.tmax", "examples.tmax.env", ["TMaxEnv"]),
    ("examples.tmax", "examples.tmax.rollouter", ["TMaxRollouter"]),
    ("examples.tmax", "examples.tmax.rubric", ["RewardTMax"]),
])
def test_public_exports_keep_the_original_objects(package, source, names):
    prefix = "torchtitan.experiments.rl"
    exported = import_module(f"{prefix}.{package}" if package else prefix)
    original = import_module(f"{prefix}.{source}")
    for name in names:
        assert getattr(exported, name) is getattr(original, name)
        assert exported.__dict__[name] is getattr(original, name)
    with pytest.raises(AttributeError):
        exported.unknown_public_export
