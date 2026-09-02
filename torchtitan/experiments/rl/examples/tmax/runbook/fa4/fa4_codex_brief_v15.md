# Task: keep growing the small copy test — steps 1 and 2 are clear

## Background

We use FlashAttention-4's CuTe DSL kernels in a training stack. On our B300
machines its backward pass does not finish; forward is fine. This is routine
triage on a dependency, aimed at a useful bug report.

Last round wrote a tiny standalone program: one block, one thread, one
global-to-shared copy of a tensor tile, one wait. It completed for every tile
shape and size we tried, including the exact shape the backward pass uses. So
the copy itself is fine, and something about the context it runs in is not.

Reuse those files rather than rewriting them:
`tma_one_copy.py`, `run_tma_one_copy_matrix.sh`, `tma_matrix_results.txt`.

## What to do

Grow that program one step at a time, running it after each step, and note
whether it completes or times out. Stop at the first step where it stops
completing.

**Steps 1 and 2 are done and both complete.** Two copies (Q then dO, separate
waits, the loop's order) and three copies (Q, dO, LSE, separate waits) both
finish normally — `--copies 2` and `--copies 3`, `HOST_COMPLETE`, shape
(128,128), stride (1,128). So neither a second nor a third copy in flight is the
trigger. **Start at step 3.**

~~2. Add the third tile the loop also loads (LSE) and its wait, so the sequence
   matches the loop body.
(done — completes)~~

3. Use the same shared-memory layout and wait-object placement the kernel uses,
   instead of freshly allocated ones: the same total allocation, the same
   per-stage offsets, the wait objects at the same relative addresses, and the
   same number of stages per tile. Allocation size and address alignment are
   both worth varying here if the first attempt completes.
4. Then the remaining setup the kernel performs before its main loop, added one
   piece at a time — the tensor-memory allocation, then the cluster launch
   configuration, then the warp specialization that splits loading from
   computing.

If some step changes the outcome, keep everything else fixed and narrow within
it: how many copies, what order, shared or separate waits, how much shared
memory, which offsets.

## Settled already, no need to recheck

The failure is deterministic — 10 of 10 runs. Not the library version (two of
them), not the PyTorch layer, not the 2-CTA path, not compile time, not GPU
sharing, not tensor sizes. Unchanged by extra barriers or fences around the
earlier wait, by delays at seven different points, by `defer_sync=False`, by
enabling `cta_layout_vmnk`, by compiler optimization levels 0-3, and by using
the CUDA 13.4 toolchain instead of 12.9. Diagnostic printing does not change the
outcome, so `cute.printf` is safe to use for observation.

What we know about where it stops: the loading warp gets past its turn-taking
step for the dO tile and then stops while issuing that copy. The Q tile copy in
the same warp and the same iteration issues normally.

## Practical notes

GPU 6 or 7 only. `timeout 180` on every run and `pkill -9 -f` afterwards. Work
under `/scratch/gpfs/TRIDAO/al9080/fa4-fix/`. Copy any library file to `orig/`
before editing, and restore the installed library byte-for-byte at the end,
confirming by hash.

## Report

A table of step versus complete/timeout, the first step that changes it, and how
you narrowed within that step. If every step completes and only the full kernel
hangs, say so — then the next move is removing pieces from the kernel rather
than adding them to the test.
