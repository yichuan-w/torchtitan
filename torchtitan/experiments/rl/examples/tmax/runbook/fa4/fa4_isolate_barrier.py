#!/usr/bin/env python3
"""Separate the tensor-memory allocation from the barrier that sits beside it.

Four results are on the table, and the reading that got written down —
"a live allocation during the loop is what stalls" — does not survive them:

    allocate before the loop, free after     hang
    allocate only after the loop             hang    <- nothing live during it
    allocate and free before the loop        complete
    never allocate                           complete

One thing does explain all four. The release sequence contains a NamedBarrier
covering every warp, and it sits *after* the loop in the two hanging rows and
*before* it in the passing one. So the variable that moved may be the barrier's
position, not the allocation — which would make this the reproducer's own bug,
the same shape as the warp-divergent polling that produced round 18's false
positive.

Two flags settle it:

  --no-release-barrier   keep the allocation live across the loop but drop the
                         barrier from the release. Completing means the barrier
                         was the cause and tensor memory is innocent.
  --post-loop-barrier    no tensor memory at all, just the same barrier after
                         the loop. Hanging proves it outright, with nothing
                         about tensor memory left in the program.
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

TARGET = pathlib.Path("/scratch/gpfs/TRIDAO/al9080/fa4-fix/tma_one_copy.py")
BACKUP = TARGET.with_suffix(".py.pre_isolate_barrier")

# Hoist the barrier out of the tensor-memory guard so it can exist alone.
DEFN_OLD = """        if const_expr(self.use_tmem):
            tmem_holding_buf = smem.allocate_array(Int32, num_elems=1,
                                                   byte_alignment=4)

            # Match the real backward kernel's tensor-memory lifetime: allocate
            # all columns before the loop and free them after every warp exits.
            tmem_alloc_barrier = pipeline.NamedBarrier(
                barrier_id=1, num_threads=32 * self.warps)
            tmem = cutlass.utils.TmemAllocator(
"""

DEFN_NEW = """        if const_expr(self.use_tmem or self.post_loop_barrier):
            tmem_alloc_barrier = pipeline.NamedBarrier(
                barrier_id=1, num_threads=32 * self.warps)

        if const_expr(self.use_tmem):
            tmem_holding_buf = smem.allocate_array(Int32, num_elems=1,
                                                   byte_alignment=4)

            # Match the real backward kernel's tensor-memory lifetime: allocate
            # all columns before the loop and free them after every warp exits.
            tmem = cutlass.utils.TmemAllocator(
"""

# Make the barrier in the release sequence optional.
REL_OLD = """            tmem.relinquish_alloc_permit()
            tmem_alloc_barrier.arrive_and_wait()
            tmem.free(tmem_ptr)


def main() -> None:
"""

REL_NEW = """            tmem.relinquish_alloc_permit()
            if const_expr(not self.no_release_barrier):
                tmem_alloc_barrier.arrive_and_wait()
            tmem.free(tmem_ptr)

        # The barrier on its own, with no tensor memory anywhere in the program.
        if const_expr(self.post_loop_barrier and not self.use_tmem):
            tmem_alloc_barrier.arrive_and_wait()


def main() -> None:
"""

INIT_OLD = "        self.tmem_free_early = False\n"
INIT_NEW = ("        self.tmem_free_early = False\n"
            "        self.no_release_barrier = False\n"
            "        self.post_loop_barrier = False\n")

ARG_OLD = '    ap.add_argument("--tmem-free-early", action="store_true")\n'
ARG_NEW = ('    ap.add_argument("--tmem-free-early", action="store_true")\n'
           '    ap.add_argument("--no-release-barrier", action="store_true")\n'
           '    ap.add_argument("--post-loop-barrier", action="store_true")\n')

SET_OLD = "    test.tmem_free_early = args.tmem_free_early\n"
SET_NEW = ("    test.tmem_free_early = args.tmem_free_early\n"
           "    test.no_release_barrier = args.no_release_barrier\n"
           "    test.post_loop_barrier = args.post_loop_barrier\n")


def main() -> None:
    text = TARGET.read_text()
    if "post_loop_barrier" in text:
        print("already present")
        return
    for old, new in ((DEFN_OLD, DEFN_NEW), (REL_OLD, REL_NEW),
                     (INIT_OLD, INIT_NEW), (ARG_OLD, ARG_NEW),
                     (SET_OLD, SET_NEW)):
        if old not in text:
            sys.exit(f"anchor not found: {old.strip()[:70]}")
        text = text.replace(old, new, 1)
    ast.parse(text)
    shutil.copy2(TARGET, BACKUP)
    TARGET.write_text(text)
    print(f"two flags added; backup at {BACKUP.name}")


if __name__ == "__main__":
    main()
