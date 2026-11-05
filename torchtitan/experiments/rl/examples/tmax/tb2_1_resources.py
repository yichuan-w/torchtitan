# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Terminal-Bench 2.1 Daytona sizes that replace the ones a task declares.

``prepare_tb2_1_data`` writes them into the rows it builds, and ``TMaxDataset``
applies them again at load to any TB-2.1 row, so an eval JSONL built before an
entry was added still runs at the corrected size.
"""

# Per-task Daytona sizes that replace the declared ones. Every task declares a 10 GB
# disk, which Daytona enforces (Harbor's Modal and Docker backends do not); in the
# 2026-10-25 eval the tasks below filled it in 33 trials (CUDA torch installs,
# httpstan's fit cache), twice inside the verifier's own install. They get 20 GB.
# extract-moves-from-video also declares 1 CPU / 2 GB for decoding a video frame by
# frame: 3 of 5 trials showed OOM and a fourth lost its sandbox ("failed to resolve
# container IP"), so it gets 2 CPU / 4 GB as well. compile-compcert (coq build at
# 4 GB) and mteb-leaderboard (loading results at 8 GB) were OOM-killed in 3 of 5
# trials each, so they get 4 GB more memory.
DISK_20GB = {"daytona_disk_gb": 20}
RESOURCE_OVERRIDES: dict[str, dict[str, int]] = {
    "extract-moves-from-video": {"daytona_cpu": 2, "daytona_mem_gb": 4, **DISK_20GB},
    "compile-compcert": {"daytona_mem_gb": 8},
    "mteb-leaderboard": {"daytona_mem_gb": 12},
    **{
        task_id: DISK_20GB
        for task_id in (
            "chess-best-move",
            "code-from-image",
            "count-dataset-tokens",
            "gcode-to-text",
            "gpt2-codegolf",
            "pytorch-model-cli",
            "pytorch-model-recovery",
            "rstan-to-pystan",
            "sam-cell-seg",
            "torch-pipeline-parallelism",
            "torch-tensor-parallelism",
        )
    },
}


def resource_override(md: dict) -> dict[str, int]:
    """The override for a TB-2.1 row's metadata, or {} (other benchmarks never match)."""
    if md.get("tb_version") != "2.1":
        return {}
    return RESOURCE_OVERRIDES.get(md.get("instance_id") or "", {})
