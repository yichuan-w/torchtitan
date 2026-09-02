# Task: FA4 backward on B300 (SM103) — round 11, the dO TMA issue stalls

## What round 10 found, and why it changes the target

Instrumenting all three warps showed the load warp stops first. It **succeeds**
at the dO `producer_acquire` for iteration 1 and then stalls **while issuing the
dO TMA load** — `LOAD dO issued` and `LOAD dO committed` never print. MMA's wait
on the dO stage and compute's wait on dPsum are both downstream of that.

```
load warp: dO cp.async.bulk.tensor issue stalls
  ├─ MMA warp: pipeline_dO.consumer_wait
  └─ compute warp: pipeline_dPsum.consumer_wait   (sequenced after the dO issue)
```

So nine rounds of consumer-side work were aimed at the wrong end. The endpoint
is the dO TMA copy itself, which by then is waiting on nothing in software.

**And the control is in the same trace: the Q TMA issue succeeds.**
`LOAD Q committed m=1 i=1` prints normally. TMA works on this hardware. Only dO's
does not.

## The question

**What is different about dO's TMA compared with Q's?** Compare them
exhaustively — they are the same kind of operation in the same warp, one works
and one hangs:

- the tensor map / descriptor: how each is built, where it lives, whether a
  `fence.proxy.tensormap` or prefetch is issued before first use, and whether
  that differs between them;
- the copy atom and its `tma_copy_bytes` / `expect_tx` accounting;
- the shapes, strides, alignment and box dimensions each descriptor carries, and
  whether dO's has a dimension that is zero, negative, unaligned, or larger than
  the descriptor allows;
- the mbarrier each is tied to, and whether dO's is initialized before the issue
  (recall CUTLASS 4.3.1 fixed an SM103 bug that was a missing `elect_one` on a
  prefetch barrier's initialization);
- whether dO's issue sits under a predicate or in a divergent region that Q's
  does not.

Print the descriptor fields at runtime if you can reach them. A TMA that never
issues usually means a malformed descriptor or a tensormap the TMA unit cannot
see yet — both are visible in what was built, not in what waits.

## Method

`cute.printf` is a safe diagnostic here: the hang is deterministic (10/10 on the
unmodified kernel) and printing does not change the outcome. Instrument the dO
and Q descriptor construction and the moment of issue, run the smallest
configuration under `timeout 180`, and compare the two side by side.

If the difference is a missing fence or a descriptor built after first use,
adding what is missing is a legitimate fix — say what ordering it enforces.

## Established, do not re-derive

Deterministic hang 10/10. Not version (`b26`, `fb02fc8`), not the PyTorch shim,
not 2-CTA, not compile time, not contention, not shape. No effect: warp/block
barriers around the LSE wait, `fence_acq_rel_cta`, inline asm with memory
clobber, `clock64` delays, `defer_sync=False` on LSE/dPsum, enabling
`cta_layout_vmnk` on LSE/dPsum, ptxas opt-level 0-3, forcing CUDA 13.4 ptxas,
one-bit phase parity, dO/dPsum stages 1→2, a dedicated dO consumer handle. MLIR
is identical between instrumented and plain builds. **The "prints make it pass"
premise is retracted** — printing changes nothing.

## Constraints

Do not remove, weaken or skip synchronization. Do not edit
`fa4_bwd_acceptance.py`. GPU 6/7 only, `timeout 180` per run, `pkill -9 -f`
after a timeout, restore the tree when done.

**A fix claim requires the acceptance script to pass with no `cute.printf` and
no probe present, and it will be re-run independently.**

## Report

The concrete difference between the dO and Q TMA paths, and if you fix it, what
was missing and why it matters on SM103 and not SM100.
