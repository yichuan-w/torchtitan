# Task: FA4 backward on B300 (SM103) — round 12, isolate the dO TMA

## Where the last two rounds landed

Round 10 traced all three warps and found the **load warp stops first**: it
succeeds at the dO `producer_acquire` for iteration 1 and then stalls **while
issuing the dO TMA copy** — `LOAD dO issued` and `LOAD dO committed` never
print. MMA's wait on the dO stage and compute's wait on dPsum are both
downstream of that. The Q TMA in the same warp issues normally.

Round 11 compared the dO and Q descriptors exhaustively and found no
malformation: no zero, negative, oversized or unaligned box dimension; both are
launch-time tensor-map arguments; both are prefetched; neither is dynamically
modified, so `fence.proxy.tensormap` is not required. Moving the dO prefetch
ahead of Q, moving the main-loop dO issue ahead of Q and LSE, and materializing
the broadcast/zero-stride `dout` all still hang.

The one difference it could name is that dO's tensor map uses a **mode-0
contiguous `(D, S, …)` encoding** — the transposition `P.T @ dO` requires — where
Q's does not.

## This round: reduce it to a standalone reproducer

Take that TMA out of the kernel. Write the smallest possible CuTe DSL program
that does nothing but issue one `cp.async.bulk.tensor` with a descriptor shaped
like dO's, into shared memory, and wait on its mbarrier. No attention, no
pipelines, no other warps.

Then vary one thing at a time and record hang or complete:

1. dO-shaped descriptor (mode-0 contiguous, transposed layout) — the suspect.
2. Q-shaped descriptor with the same element count — the control that should
   complete.
3. The dO shape with the transposition removed, if that is expressible.
4. The dO shape at smaller box dimensions, to find whether a size threshold
   exists.
5. If a threshold appears, bisect it.

A standalone reproducer that hangs would reduce this from "FA4's backward hangs"
to "a TMA copy with this descriptor shape does not issue on SM103", which is
something NVIDIA can act on directly and which anyone can verify in seconds.

If the standalone copy **completes**, that is equally decisive in the other
direction: the descriptor is fine in isolation and the problem is in how the
kernel's surrounding state — TMEM allocation, cluster configuration, the other
pipelines' barriers — interacts with it. Then say which of those you would
suspect and why.

## Established, do not re-derive

Deterministic hang, 10/10 on the unmodified kernel, smallest configuration. Not
version (`b26` and source `fb02fc8`), not the PyTorch shim, not 2-CTA, not
compile time, not contention, not shape. No effect from: warp and block barriers
around the LSE wait, `fence_acq_rel_cta`, inline asm with memory clobber,
`clock64` delays at seven sites, `defer_sync=False` on LSE/dPsum, enabling
`cta_layout_vmnk` on LSE/dPsum, ptxas `opt-level` 0-3, forcing the CUDA 13.4
ptxas over Triton's 12.9, one-bit phase parity, dO/dPsum stages 1→2, a dedicated
dO consumer handle. MLIR is identical between instrumented and plain builds.

**The earlier "prints make it pass" premise is retracted** — printing changes
nothing, which is why `cute.printf` is now a safe diagnostic and how round 10's
trace was possible.

## Constraints

Do not remove, weaken or skip synchronization as a fix. Do not edit
`fa4_bwd_acceptance.py`. GPU 6/7 only, `timeout 180` per run, `pkill -9 -f`
after a timeout, restore the installed tree when done and say so.

A fix claim requires the acceptance script to pass with no `cute.printf` and no
probe present, and it will be re-run independently.

## Report

The reproducer, the variant table with hang/complete per row, and what it
implies. A minimal standalone hang is the best outcome; a clean standalone
completion is the second best, because it moves the search into the kernel's
surrounding state and says so.
