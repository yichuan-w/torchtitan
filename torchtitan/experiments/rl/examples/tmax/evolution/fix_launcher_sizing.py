#!/usr/bin/env python3
"""Put our local sizing in the launcher, where it belongs.

The recipe now defaults to upstream's numbers so the shared checkout stays
neutral for Yichuan. That leaves this script as the place that says how to fit
a box where five of eight GPUs are someone else's: a single-GPU generator and a
share of memory small enough to sit beside a resident job.

Usage: fix_launcher_sizing.py <path to smoke1_sized.sh>
"""
from __future__ import annotations

import pathlib
import shutil
import sys

OLD = 'export SWE_GEN_TP="${SWE_GEN_TP:-1}"'
NEW = ('export SWE_GEN_TP="${SWE_GEN_TP:-1}"\n'
       '# Recipe default is 0.9, which assumes the card is ours. It is not.\n'
       'export SWE_GPU_MEM_LIMIT="${SWE_GPU_MEM_LIMIT:-0.35}"')


def main() -> None:
    target = pathlib.Path(sys.argv[1])
    text = target.read_text()
    if "SWE_GPU_MEM_LIMIT" in text:
        print("already sets the memory share")
        return
    if OLD not in text:
        sys.exit("could not find the generator sizing line")
    shutil.copy2(target, target.with_suffix(".sh.pre_sizing"))
    target.write_text(text.replace(OLD, NEW, 1))
    print("launcher now carries the local sizing")


if __name__ == "__main__":
    main()
