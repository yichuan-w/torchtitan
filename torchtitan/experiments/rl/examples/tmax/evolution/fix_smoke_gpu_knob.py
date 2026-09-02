#!/usr/bin/env python3
"""Point the smoke script at the GPU knob that actually works.

`train.py`'s allocator gives each actor its own `CUDA_VISIBLE_DEVICES`, built
from a contiguous block of absolute indices starting at `RL_GPU_OFFSET`. That
overrides whatever the launching shell set, so every run so far that looked
pinned to the free GPUs was really sharing 0-2 with other users' resident jobs —
which is also why a generator asking for 35% of a card reported only 21 GiB
free.

Usage: fix_smoke_gpu_knob.py <path to smoke1_sized.sh>
"""
from __future__ import annotations

import pathlib
import shutil
import sys

OLD = 'export CUDA_VISIBLE_DEVICES="${SMOKE_GPUS:-0,6,7}"'
NEW = (
    "# The actors set their own CUDA_VISIBLE_DEVICES from absolute indices, so\n"
    "# setting it here does nothing. RL_GPU_OFFSET shifts the block they take.\n"
    'export RL_GPU_OFFSET="${RL_GPU_OFFSET:-0}"'
)


def main() -> None:
    target = pathlib.Path(sys.argv[1])
    text = target.read_text()
    if "RL_GPU_OFFSET" in text:
        print("already using RL_GPU_OFFSET")
        return
    if OLD not in text:
        sys.exit("could not find the CUDA_VISIBLE_DEVICES line")
    text = text.replace(OLD, NEW, 1)
    text = text.replace("gpus=$CUDA_VISIBLE_DEVICES", "gpu_offset=$RL_GPU_OFFSET")
    shutil.copy2(target, target.with_suffix(".sh.pre_gpu_knob"))
    target.write_text(text)
    print("smoke1_sized.sh now uses RL_GPU_OFFSET")


if __name__ == "__main__":
    main()
