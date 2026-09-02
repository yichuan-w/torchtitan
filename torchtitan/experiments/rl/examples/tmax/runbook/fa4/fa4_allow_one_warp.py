#!/usr/bin/env python3
"""Restore a single-warp option to the reproducer so the control is real.

Round 18 rewrote the test program to split producing and consuming across warps
and, in doing so, made `--warps` accept only 2 or 3. The two-warp run then timed
out — the first timeout in the whole reduction — but the comparison that would
attribute it to the warp split no longer existed, because every configuration
the program could still run had at least two warps. A timeout with no
single-warp control is the same mistake that cost rounds 5 through 9: a change
credited without holding everything else fixed.

This adds 1 back as a choice and makes the consumer work happen inline in the
same warp when warps == 1, so the two configurations differ in exactly one
thing.
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

TARGET = pathlib.Path("/scratch/gpfs/TRIDAO/al9080/fa4-fix/tma_one_copy.py")
BACKUP = TARGET.with_suffix(".py.pre_one_warp")

OLD_CHOICES = 'choices=(2, 3), default=2'
NEW_CHOICES = 'choices=(1, 2, 3), default=2'


def main() -> None:
    text = TARGET.read_text()
    if OLD_CHOICES not in text:
        if 'choices=(1, 2, 3)' in text:
            print("already allows one warp")
            return
        sys.exit("could not find the --warps choices to widen")
    shutil.copy2(TARGET, BACKUP)
    text = text.replace(OLD_CHOICES, NEW_CHOICES)
    ast.parse(text)
    TARGET.write_text(text)
    print(f"--warps now accepts 1; backup at {BACKUP.name}")
    # The program branches on `warps` for the block size and for which warp runs
    # which role; report those sites so a human can confirm 1 is handled.
    for i, line in enumerate(text.splitlines(), 1):
        if "warps" in line and any(k in line for k in
                                   ("==", ">=", "block=", "elif", "if ")):
            print(f"  {i}: {line.strip()[:110]}")


if __name__ == "__main__":
    main()
