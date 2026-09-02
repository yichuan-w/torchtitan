# Task: FA4 backward hang on B300 (SM103) — fifth attempt

## Goal

```
cd /scratch/gpfs/TRIDAO/al9080/fa4-fix
CUDA_VISIBLE_DEVICES=6 /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python fa4_bwd_acceptance.py
```

`ALL PASS (12/12)` with no `cute.printf` and no delay probe in the modified
files at the time of the run. Verify that yourself before claiming it.

Naming the mechanism precisely is worth as much as a fix. Do not paper over the
hang.

## Your last round was right, and it changes the hypothesis

You showed that `clock64` delays at seven sites — before the main-loop dO
acquire, after dO commit, after the dPsum advance, before the prologue acquire,
at every former trace site, restricted to block 0 and the traced lane, and
synchronizing load-warp startup against TMEM allocation — **all still hang**.
So the earlier "printf fixes it by slowing the load warp" reading was wrong, and
the timing-race framing built on it is withdrawn.

That leaves the question of what `cute.printf` does that a delay does not. The
obvious candidate: the prints read `producer_state_dO_dPsum.index` and
`.phase` and pass them out of the kernel. That forces those values to be
materialized and stops the compiler from reordering, sinking, or eliding the
computations behind them. A delay forces nothing.

**So the working hypothesis is now codegen, not runtime ordering:** without an
observer, something in this loop is compiled in a way that never satisfies the
dO consumer wait on SM103.

## Two experiments, in this order

**1. Materialize without printing or delaying.** Take the same two values at the
same sites and force them to be kept — a volatile store to a scratch global,
consuming them in an opaque way the optimizer cannot see through, an inline-asm
input operand, whatever this DSL supports. No I/O, no delay.

- Still hangs → materialization is not what the print was doing; report that and
  the hypothesis dies cleanly.
- Passes → the bug is codegen. Then narrow it: is it `.index`, `.phase`, or
  both? One site or all of them? The smallest set that changes the outcome is
  the finding.

**2. Diff the generated IR.** `CUTE_DSL_DUMP_DIR` makes the DSL write out its
MLIR. Dump one build with the prints and one without, diff the region around the
dO pipeline's wait, and look for the difference that is not the printing itself
— a hoisted load, a dropped update to the phase, a wait folded against a
constant. This may answer the question directly, and it costs one run per build
rather than a search.

## Established, do not re-derive

* Not a version regression (`4.0.0b26` and source `fb02fc8` both hang), not the
  PyTorch shim, not 2-CTA, not compilation time, not contention, not shape —
  D=64/128, S=256/512/2048, causal and not, all hang.
* Stall point: MMA warp completes Q and dS for iteration 2, then blocks in
  `pipeline_dO.consumer_wait`. Q and dS advance; dO does not.
* Insufficient: one-bit phase parity normalization; dO/dPsum stage count 1→2;
  a dedicated `pipeline_dO` consumer handle with explicit `wait_and_advance()`
  and `release()` in the MMA loop.
* `fa4-fix/with-trace-flash_bwd_sm100.py` is the version that passes *with*
  prints — it is evidence, keep it.
* B300 SXM6, capability (10, 3) = SM103. Forward has explicit SM103 handling,
  the backward files have none.

## Constraints

Do not remove, weaken, or skip any synchronization primitive. Adding
synchronization is allowed if you can say what ordering it enforces. Do not edit
`fa4_bwd_acceptance.py`, do not route SM103 elsewhere, do not disable FA4
backward. GPU 6 and 7 only, `timeout 180` on every run, `pkill -9 -f` after a
timeout. Copy originals to `fa4-fix/orig/`, diffs to `fa4-fix/patches/`.

## Reporting

Whichever way experiment 1 goes, say so plainly — a cleanly killed hypothesis is
a result. If the IR diff shows the difference, quote the relevant lines. If you
end without a fix, the deliverable is the narrowed mechanism plus the evidence.
