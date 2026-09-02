#!/usr/bin/env python3
"""Add a variant that allocates tensor memory and releases it before the loop.

The current test holds the allocation across the whole loop. That leaves two
explanations for the stall it produces, and they call for different reports:
the allocation being *live* while the copies run, or the act of allocating at
all having some lasting effect on the kernel. Releasing immediately separates
them — if the loop then completes, only a live allocation matters.

Adds `--tmem-free-early`, which allocates, waits, retrieves the pointer,
relinquishes and frees, all before the first iteration, and skips the release
at the end so nothing is freed twice.
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

TARGET = pathlib.Path("/scratch/gpfs/TRIDAO/al9080/fa4-fix/tma_one_copy.py")
BACKUP = TARGET.with_suffix(".py.pre_free_early")

ALLOC_BLOCK = """        if const_expr(self.use_tmem and not self.tmem_after_loop):
            tmem.allocate(self.tmem_cols)
            tmem.wait_for_alloc()
            tmem_ptr = tmem.retrieve_ptr(cutlass.Float32)
"""

ALLOC_REPLACEMENT = """        if const_expr(self.use_tmem and not self.tmem_after_loop):
            tmem.allocate(self.tmem_cols)
            tmem.wait_for_alloc()
            tmem_ptr = tmem.retrieve_ptr(cutlass.Float32)
            # Release before the loop rather than after it, so the loop runs
            # with no live allocation while the allocation still happened.
            if const_expr(self.tmem_free_early):
                tmem.relinquish_alloc_permit()
                tmem_alloc_barrier.arrive_and_wait()
                tmem.free(tmem_ptr)
"""

RELEASE_BLOCK = """        if const_expr(self.use_tmem):
            if const_expr(self.tmem_after_loop):"""

RELEASE_REPLACEMENT = """        if const_expr(self.use_tmem and not self.tmem_free_early):
            if const_expr(self.tmem_after_loop):"""

INIT_ANCHOR = "        self.tmem_after_loop = False\n"
INIT_REPLACEMENT = ("        self.tmem_after_loop = False\n"
                    "        self.tmem_free_early = False\n")

ARG_ANCHOR = '    ap.add_argument("--tmem-after-loop", action="store_true")\n'
ARG_REPLACEMENT = ('    ap.add_argument("--tmem-after-loop", action="store_true")\n'
                   '    ap.add_argument("--tmem-free-early", action="store_true")\n')

SET_ANCHOR = "    test.tmem_after_loop = args.tmem_after_loop\n"
SET_REPLACEMENT = ("    test.tmem_after_loop = args.tmem_after_loop\n"
                   "    test.tmem_free_early = args.tmem_free_early\n")


def main() -> None:
    text = TARGET.read_text()
    if "tmem_free_early" in text:
        print("already present")
        return
    for old, new in ((ALLOC_BLOCK, ALLOC_REPLACEMENT),
                     (RELEASE_BLOCK, RELEASE_REPLACEMENT),
                     (INIT_ANCHOR, INIT_REPLACEMENT),
                     (ARG_ANCHOR, ARG_REPLACEMENT),
                     (SET_ANCHOR, SET_REPLACEMENT)):
        if old not in text:
            sys.exit(f"anchor not found: {old.strip()[:60]}")
        text = text.replace(old, new, 1)
    ast.parse(text)
    shutil.copy2(TARGET, BACKUP)
    TARGET.write_text(text)
    print(f"--tmem-free-early added; backup at {BACKUP.name}")


if __name__ == "__main__":
    main()
