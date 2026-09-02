#!/usr/bin/env python3
"""Ask why a block-wide sync after the copy loop never releases.

The post-loop NamedBarrier hangs on H100 as well as B300, so it is not evidence
about SM103 — it is either a mistake in this test program or a real constraint
on syncing after bulk copies. Two variants separate those, and the answer
matters beyond the test: the library's backward pass uses named barriers with
fixed ids around the same copies.

  --barrier-id N            use a different named barrier. The current id is 1,
                            and the pipeline and allocator machinery claim ids
                            of their own; a collision would produce exactly this
                            symptom, with two unrelated pieces of code waiting
                            on the same counter.
  --post-loop-syncthreads   sync the whole block the ordinary way instead. If
                            this completes where the named barrier hangs, the
                            problem is the barrier, not syncing after copies.
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

TARGET = pathlib.Path("/scratch/gpfs/TRIDAO/al9080/fa4-fix/tma_one_copy.py")
BACKUP = TARGET.with_suffix(".py.pre_barrier_variants")

DEFN_OLD = """                barrier_id=1, num_threads=32 * self.warps)"""
DEFN_NEW = """                barrier_id=self.barrier_id, num_threads=32 * self.warps)"""

POST_OLD = """        # The barrier on its own, with no tensor memory anywhere in the program.
        if const_expr(self.post_loop_barrier and not self.use_tmem):
            tmem_alloc_barrier.arrive_and_wait()
"""
POST_NEW = """        # The barrier on its own, with no tensor memory anywhere in the program.
        if const_expr(self.post_loop_barrier and not self.use_tmem):
            tmem_alloc_barrier.arrive_and_wait()

        if const_expr(self.post_loop_syncthreads):
            cute.arch.barrier()
"""

INIT_OLD = "        self.post_loop_barrier = False\n"
INIT_NEW = ("        self.post_loop_barrier = False\n"
            "        self.post_loop_syncthreads = False\n"
            "        self.barrier_id = 1\n")

ARG_OLD = '    ap.add_argument("--post-loop-barrier", action="store_true")\n'
ARG_NEW = ('    ap.add_argument("--post-loop-barrier", action="store_true")\n'
           '    ap.add_argument("--post-loop-syncthreads", action="store_true")\n'
           '    ap.add_argument("--barrier-id", type=int, default=1)\n')

SET_OLD = "    test.post_loop_barrier = args.post_loop_barrier\n"
SET_NEW = ("    test.post_loop_barrier = args.post_loop_barrier\n"
           "    test.post_loop_syncthreads = args.post_loop_syncthreads\n"
           "    test.barrier_id = args.barrier_id\n")


def main() -> None:
    text = TARGET.read_text()
    if "post_loop_syncthreads" in text:
        print("already present")
        return
    for old, new in ((DEFN_OLD, DEFN_NEW), (POST_OLD, POST_NEW),
                     (INIT_OLD, INIT_NEW), (ARG_OLD, ARG_NEW),
                     (SET_OLD, SET_NEW)):
        if old not in text:
            sys.exit(f"anchor not found: {old.strip()[:70]}")
        text = text.replace(old, new, 1)
    ast.parse(text)
    shutil.copy2(TARGET, BACKUP)
    TARGET.write_text(text)
    print(f"barrier variants added; backup at {BACKUP.name}")


if __name__ == "__main__":
    main()
