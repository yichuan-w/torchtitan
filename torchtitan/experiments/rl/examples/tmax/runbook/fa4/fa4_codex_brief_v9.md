# Task: FA4 backward on B300 (SM103) — ninth attempt, below MLIR

## Why this round exists

Eight rounds narrowed the hang to one reproducible anomaly and then stopped,
because every explanation available *above* the machine code was eliminated. The
one layer never inspected is the generated code itself. That is this round's
whole job.

## The anomaly

In the compute warp, a **pair** of single-lane predicated `cute.printf` calls
straddling the LSE prefetch wait makes all twelve configurations pass:

```python
# flash_bwd_sm100.py, "# Prefetch 1 stage of LSE"
<print>            # guarded: block 0, thread == compute_warp_ids[0] * 32
pipeline_LSE.consumer_wait(consumer_state_LSE)
<print>            # same guard
```

Everything else hangs: either print alone; the same pair on all 32 lanes;
single-lane guarded empty asm with `~{memory}`; a guarded non-elidable `%smid`
read; `cute.arch.sync_warp()` predicated or not; `fence_acq_rel_cta()`;
`clock64` spins at seven sites; producer-side materialization of `.index` and
`.phase`. MLIR is structurally identical with and without the prints around both
the producer region and the LSE wait.

The passing file is preserved at `fa4-fix/with-trace-flash_bwd_sm100.py`; the
pristine one at `fa4-fix/orig/flash_bwd_sm100.py`.

## What to do

Get the generated machine code for both builds and diff it around that wait.

1. Find where the DSL writes its cubin or PTX — `CUTE_DSL_DUMP_DIR` already
   produces MLIR, so check whether it also emits PTX/cubin, and if not, look for
   a JIT cache directory holding compiled artifacts.
2. Disassemble both (`cuobjdump -sass`, `nvdisasm`) and diff the region around
   the LSE `mbarrier.try_wait.parity`.
3. Ignore differences that are only the print sequence itself, register
   numbering, or address shifts. Look for a difference in **control flow or
   scheduling** around the wait: a branch that exists in one and not the other,
   a reconvergence point, `BSSY`/`BSYNC` placement, a `WARPSYNC`, predicate
   register handling, or the wait being hoisted/sunk relative to its neighbours.
4. Report the difference verbatim. If it names something a person could fix in
   the kernel, propose that as a patch and validate it with the acceptance
   script.

If the SASS is also structurally identical apart from the prints, that is a
strong and reportable result: it would put the difference at runtime scheduling
rather than in generated code, and it is the last thing this line of
investigation can establish.

## Constraints

Do not remove, weaken or skip synchronization as a fix. A `cute.printf` left in
the kernel is not a fix. Do not edit `fa4_bwd_acceptance.py`. GPU 6/7 only,
`timeout 180` per run, `pkill -9 -f` after a timeout. Restore the installed tree
byte-for-byte before finishing, and say so.

## Reporting

The SASS diff around the LSE wait, what it shows, and either a patch validated
by the acceptance script or a clear statement that the generated code does not
explain the difference either.
