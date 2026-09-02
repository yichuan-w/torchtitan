# Task: FA4 backward hang on B300 (SM103) — sixth attempt, by minimization

## Change of method: stop searching forward, minimize backward

Four rounds of hypothesis-and-test have killed every hypothesis, and each one
was killed properly. But there is an asset those rounds produced that has not
been used: **a version of `flash_bwd_sm100.py` that passes all 12
configurations**, preserved at
`/scratch/gpfs/TRIDAO/al9080/fa4-fix/with-trace-flash_bwd_sm100.py`.

Do not look for the bug. **Minimize that file against the original until only
the changes that matter remain.** Delta debugging converges; hypothesis search
has not.

## The procedure

The passing file differs from `fa4-fix/orig/flash_bwd_sm100.py` in two kinds of
change: `cute.printf` trace sites, and a structural change that gives
`pipeline_dO` its own consumer handle driven by explicit `wait_and_advance()` /
`release()` in the MMA loop.

Three subsets are already tested, so start from what is known:

| subset | result |
|---|---|
| everything (prints + structural change) | **passes** |
| structural change only, all prints removed | hangs |
| producer-side prints only | hangs |

So the load-bearing piece is neither the structural change alone nor the
producer prints. The untested region is the **consumer-side** changes and the
combinations involving them. Bisect there:

1. Structural change + consumer-side prints only.
2. If that passes, drop consumer prints one at a time until it hangs. The last
   one you removed is load-bearing.
3. If it hangs, add back the smallest other piece until it passes.

Keep a table of subset → pass/hang as you go, and put it in your report. That
table is the deliverable even if you stop before a clean fix.

## Then explain it

Once the minimal passing change is known, say what it does that the original
does not. A single print at one consumer site changing a deadlock into correct
execution is a strong signal about where the missing ordering or the miscompile
lives, and at that point the mechanism is usually one reading away.

If the minimal change turns out to be a print that cannot be removed, that is
still the answer to report: name the exact site, and quote the surrounding code.

## Established, do not re-derive

* Not a version regression (`4.0.0b26` and source `fb02fc8` both hang), not the
  PyTorch shim, not 2-CTA, not compile time, not contention, not shape.
* Not timing: `clock64` delays at seven sites, including every former trace
  site and restricted to the traced lane, all still hang.
* Not materialization: consuming `.index` and `.phase` through side-effecting
  empty inline asm at all eight producer sites still hangs.
* Not producer-side codegen: MLIR with and without the producer prints is
  structurally identical apart from the print and SSA renumbering —
  `mbarrier.try_wait.parity`, barrier pointer construction, phase operand,
  timeout, loop-carried updates, `arrive.expect_tx` and consumer waits all
  unchanged.
* Stall point: MMA warp finishes Q and dS for iteration 2, then blocks in
  `pipeline_dO.consumer_wait`.
* Insufficient on their own: one-bit phase parity; dO/dPsum stages 1→2.

## Constraints

Do not remove, weaken or skip synchronization as a *fix* (removing prints during
minimization is the whole point and is fine). Do not edit
`fa4_bwd_acceptance.py`. GPU 6 and 7 only, `timeout 180` per run, `pkill -9 -f`
after any timeout. The installed tree is pristine; keep `orig/` and
`with-trace-flash_bwd_sm100.py` intact — they are the two ends of the bisection.

## Reporting

The subset table, the minimal passing change, and your reading of what it means.
If you reach a fix that survives with no prints, run the acceptance script and
show its output.
