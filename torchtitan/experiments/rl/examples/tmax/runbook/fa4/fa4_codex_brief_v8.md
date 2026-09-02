# Task: FA4 backward on B300 (SM103) — eighth attempt, one untested variable

## The one structural element never isolated: predication

Round 7 showed that none of these, placed around
`pipeline_LSE.consumer_wait(consumer_state_LSE)` in the compute warp, reproduce
the print pair's effect:

| variant | result |
|---|---|
| empty side-effecting inline asm + `~{memory}` clobber | HANG |
| same, with `.index` and `.phase` as input operands | HANG |
| `cute.arch.fence_acq_rel_cta()` | HANG |

And the MLIR around that wait is structurally identical with and without the
prints, apart from the print calls and SSA renumbering.

But the prints are not unconditional. They sit inside

```python
if cute.arch.block_idx()[0] == 0 and \
        cute.arch.thread_idx()[0] == self.compute_warp_ids[0] * 32:
    cute.printf(...)
```

— a **single-lane predicated region**, which appears in the IR as
`scf.if %traced_lane { ... }`. A barrier every lane executes and a region only
one lane enters are different things for warp divergence and reconvergence, and
that difference has not been tested. Blackwell's independent thread scheduling
makes reconvergence points less implicit than one might assume.

## The experiment

Reproduce the **predication**, not the print.

1. Put the empty side-effecting inline asm from round 7 **inside the same
   `if block_idx()[0] == 0 and thread_idx()[0] == compute_warp_ids[0] * 32`
   guard**, before and after the LSE wait. Nothing else changes.
2. If that hangs, try an empty predicated region with any non-elidable trivial
   body the DSL allows, same guard, same two positions.
3. If that hangs, try an explicit warp-level sync (`__syncwarp` equivalent —
   `cute.arch.sync_warp()` or `barrier.warp.sync` via asm) at those two
   positions, first unconditionally and then under the guard.
4. Also worth one run: keep the two prints but change the guard so **all** lanes
   of that warp print. If that hangs while the single-lane version passes, the
   predication is confirmed as the active ingredient rather than the printing.

Record pass/hang for each; the table is the deliverable.

## What this would mean

If a predicated empty region fixes it, the kernel depends on a reconvergence
point that the original does not establish, and the fix is a warp sync at that
site rather than anything about LSE state. If step 4 hangs with all lanes
printing, that is strong confirmation and worth reporting even without a fix.

## Already ruled out — do not revisit

Version (`b26`, `fb02fc8`), the PyTorch shim, 2-CTA, compile time, contention,
shape, `clock64` delays at seven sites, producer-side materialization via inline
asm, producer-region and LSE-region MLIR differences, one-bit phase parity,
dO/dPsum stages 1→2, the dedicated dO consumer handle alone, and the three
unconditional barrier variants above.

Load-bearing change, established by minimization: the **pair** of prints
straddling the compute warp's LSE consumer wait. Removing either hangs.

## Constraints

No removing, weakening or skipping synchronization as a fix. A `cute.printf`
left in the kernel is not a fix. Do not edit `fa4_bwd_acceptance.py`. GPU 6/7
only, `timeout 180` per run, `pkill -9 -f` after a timeout, restore the
installed tree when done.
