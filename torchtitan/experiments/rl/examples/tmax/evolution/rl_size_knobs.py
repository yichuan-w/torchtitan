#!/usr/bin/env python3
"""Let the alphabet_sort recipe be sized from the environment.

The box is shared and how many GPUs are free changes hour to hour: right now
five of eight hold other people's resident jobs. The recipe hard-codes trainer
TP 2 and generator TP 2, which needs four, and editing those numbers by hand
each time is how a local sizing tweak ends up committed by accident.

Adds SWE_TRAIN_TP and SWE_GEN_TP, defaulting to what is there now, so a run can
be fitted to whatever is free without the file changing again.

Usage: rl_size_knobs.py <path to alphabet_sort/config_registry.py>
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

IMPORT_MARK = "import os"
HELPER = '''

def _env_degree(name: str, default: int) -> int:
    """Parallel degree from the environment, for fitting a shared box."""
    import os

    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default
'''

TRAIN_OLD = """                data_parallel_shard_degree=1,
                tensor_parallel_degree=2,
            ),
            checkpoint=CheckpointManager.Config(
                enable=True,
                initial_load_in_hf=True,"""
TRAIN_NEW = """                data_parallel_shard_degree=1,
                tensor_parallel_degree=_env_degree("SWE_TRAIN_TP", 2),
            ),
            checkpoint=CheckpointManager.Config(
                enable=True,
                initial_load_in_hf=True,"""

GEN_OLD = """                data_parallel_degree=1,
                tensor_parallel_degree=2,  # LOCAL: fit trainer+generator in quiet GPUs 0-3"""
GEN_NEW = """                data_parallel_degree=_env_degree("SWE_GEN_DP", 1),
                tensor_parallel_degree=_env_degree("SWE_GEN_TP", 2),  # LOCAL: shared box"""


def main() -> None:
    target = pathlib.Path(sys.argv[1])
    text = target.read_text()
    if "_env_degree" in text:
        print("knobs already present")
        return
    for old, new in ((TRAIN_OLD, TRAIN_NEW), (GEN_OLD, GEN_NEW)):
        if old not in text:
            sys.exit(f"anchor not found: {old.strip()[:60]}")
        text = text.replace(old, new, 1)

    # Put the helper after the imports so both call sites can reach it.
    lines = text.splitlines(keepends=True)
    last_import = max(i for i, l in enumerate(lines)
                      if l.startswith(("import ", "from ")))
    lines.insert(last_import + 1, HELPER)
    text = "".join(lines)

    ast.parse(text)
    shutil.copy2(target, target.with_suffix(".py.pre_size_knobs"))
    target.write_text(text)
    print("SWE_TRAIN_TP / SWE_GEN_TP / SWE_GEN_DP knobs added")


if __name__ == "__main__":
    main()
