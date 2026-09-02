# Task: B300 tensor-copy reduction, round 13 — add state back one layer at a time

## Where round 12 left it

The standalone test settled the descriptor question. One CTA, one active thread,
one global-to-shared bulk tensor copy, one barrier — and **every variant
completed**: the transposed mode-0-contiguous dO descriptor, the Q-shaped
control, dO without the transpose, and box sizes from 128×128 down to 16×16.
No size threshold appeared.

So the descriptor and the copy are valid in isolation on this hardware, and
whatever makes the library's backward pass stop depends on the state around
that copy.

Files from that round, to build on rather than redo:
`tma_one_copy.py`, `run_tma_one_copy_matrix.sh`, `tma_matrix_results.txt`.

## This round: reintroduce the surrounding state in order

Grow the reproducer one layer at a time and record complete/timeout at each
step. Stop at the first layer that stops completing — that layer is the answer.

1. **Two copies, Q then dO, on separate barriers**, matching the load warp's
   ordering. Highest priority: in the real kernel dO is issued while earlier
   tensor transactions may still be outstanding, and a failure here would point
   at scoreboard or backpressure behaviour that a single copy cannot show.
2. **Add the intervening LSE copy and its wait**, so the sequence matches the
   loop body.
3. **Reproduce the staged shared-memory allocation and barrier addresses** the
   kernel uses, rather than fresh ones.
4. **Only then** add the tensor-memory allocation and the cluster launch
   configuration.

If a layer changes the outcome, hold everything else fixed and narrow within
that layer: how many copies, which order, same barrier or different, how much
shared memory, which addresses.

## Already settled — do not spend runs on these

Deterministic: the library's backward hangs in 10 of 10 runs on the smallest
configuration. Not the library version (two independent versions), not the
PyTorch integration, not the 2-CTA path, not compilation, not GPU contention,
not tensor shapes. Unchanged by warp and block barriers around the LSE wait,
memory fences, timing delays at seven sites, `defer_sync=False`, enabling
`cta_layout_vmnk`, compiler optimization levels 0–3, and the CUDA 13.4
toolchain instead of 12.9. The generated MLIR is identical with and without
diagnostic printing, and printing does not change the outcome, which is why
`cute.printf` is safe to use as an observation tool.

The load warp reaches the dO copy's `producer_acquire` successfully and stops
while issuing the copy itself; the Q copy in the same warp and iteration issues
normally.

## Practical notes

GPU 6 or 7 only. `timeout 180` on every run, `pkill -9 -f` after a timeout.
Work under `/scratch/gpfs/TRIDAO/al9080/fa4-fix/`. Copy any library file to
`orig/` before editing and restore the installed library byte-for-byte at the
end, confirming by hash.

## Report

The layer table with complete/timeout per row, the first layer that changes the
outcome, and the narrowing you did inside it. If every layer completes and only
the full kernel hangs, say so — that is informative too, and it means the next
step is to remove state from the kernel rather than add it to the reproducer.
