#!/usr/bin/env python3
"""Make the shared recipe default to upstream's sizing, not ours.

The checkout is shared with Yichuan. On 2026-08-08 the alphabet_sort recipe was
edited to fit a box where five of eight GPUs hold other people's jobs: the
generator went from tensor parallel 4 to 2, and a `gpu_memory_limit` of 0.35 was
added, which upstream does not set at all. Today's change made those values the
defaults of new environment knobs — so pushing as-is would hand Yichuan our
local sizing as the recipe's behaviour, silently.

This flips it: the knobs default to what upstream has, and our sizing moves to
the launch script as environment variables. Someone who sets nothing gets
upstream behaviour; we set the variables and get ours.

Usage: rl_neutralize_config.py <path to alphabet_sort/config_registry.py>
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

# Upstream: generator tensor_parallel_degree=4, and no gpu_memory_limit field.
OLD_GEN = """                data_parallel_degree=_env_degree("SWE_GEN_DP", 1),
                tensor_parallel_degree=_env_degree("SWE_GEN_TP", 2),  # LOCAL: shared box"""
NEW_GEN = """                data_parallel_degree=_env_degree("SWE_GEN_DP", 1),
                tensor_parallel_degree=_env_degree("SWE_GEN_TP", 4),"""

OLD_MEM = """            gpu_memory_limit=0.35,  # LOCAL: shared box, other users resident"""
NEW_MEM = """            gpu_memory_limit=_env_float("SWE_GPU_MEM_LIMIT", 0.9),"""

HELPER = '''

def _env_float(name: str, default: float) -> float:
    """Fraction of a GPU the generator may take, from the environment.

    Kept a knob rather than a constant because how much is free depends on who
    else is on the box, and a number chosen for one afternoon should not become
    the recipe's behaviour for everyone.
    """
    import os

    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default
'''


def main() -> None:
    target = pathlib.Path(sys.argv[1])
    text = target.read_text()
    if "_env_float" in text:
        print("already neutralized")
        return
    for old, new in ((OLD_GEN, NEW_GEN), (OLD_MEM, NEW_MEM)):
        if old not in text:
            sys.exit(f"anchor not found: {old.strip()[:60]}")
        text = text.replace(old, new, 1)

    marker = "def _env_degree("
    idx = text.index(marker)
    text = text[:idx] + HELPER.lstrip("\n") + "\n" + text[idx:]

    ast.parse(text)
    shutil.copy2(target, target.with_suffix(".py.pre_neutralize"))
    target.write_text(text)
    print("defaults now match upstream; local sizing moves to the launcher")


if __name__ == "__main__":
    main()
