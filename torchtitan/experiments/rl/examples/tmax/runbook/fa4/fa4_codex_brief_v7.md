# Task: FA4 backward on B300 (SM103) — seventh attempt, one target

## What round 6 established

The load-bearing change is a **pair** of `cute.printf` calls straddling
`pipeline_LSE.consumer_wait(consumer_state_LSE)` in the compute warp, printing
`iter_idx`, `consumer_state_LSE.index` and `consumer_state_LSE.phase` on both
sides. Removing either one hangs. That pair, not the structural dO change and
not the producer prints, is what makes all 12 configurations pass.

So the stall observed at `pipeline_dO.consumer_wait` is downstream of the real
problem, which sits around the **LSE consumer wait in the compute warp**.

## The experiment

Replace that print pair with a compiler barrier that does no I/O, at exactly
that site. Round 5's inline-asm experiment was at the *producer* sites and
failed; this is a different location and is untested.

Try, in increasing strength, and record pass/hang for each:

1. Empty inline asm with a **memory clobber** immediately before and after the
   LSE wait.
2. The same, with `consumer_state_LSE.index` and `.phase` as input operands, so
   both values are live across the boundary.
3. Whatever explicit fence the DSL offers at that point (`cute.arch.fence_*`),
   before and after.

- One of them passes → that is the fix, and the mechanism is that the compiler
  was reordering or eliding something across the LSE wait. Say which barrier
  strength was needed; that distinguishes "values must stay live" from "memory
  operations must not cross".
- None passes → the print is doing something no barrier does. Report that, and
  dump the MLIR around the compute-warp LSE wait with and without the print pair
  (`CUTE_DSL_DUMP_DIR`) — round 5 diffed the producer region only, so this
  region has not been compared.

## Constraints

Do not remove, weaken or skip synchronization as a fix. A `cute.printf` left in
the kernel is not an acceptable fix; if that is where you end, say so and report
the site. Do not edit `fa4_bwd_acceptance.py`. GPU 6/7 only, `timeout 180` per
run, `pkill -9 -f` after a timeout. Restore the installed tree when done.

## Already ruled out, do not revisit

Version (`b26` and `fb02fc8`), the PyTorch shim, 2-CTA, compile time,
contention, shape, `clock64` delays at seven sites, producer-side
materialization via inline asm, producer-region MLIR differences, one-bit phase
parity, dO/dPsum stages 1→2, and the dedicated dO consumer handle on its own.

## Report

The pass/hang table for the barrier variants, the minimal thing that works, and
what it implies about what the compiler was doing. If nothing works, the MLIR
diff around the compute-warp LSE wait is the deliverable.
