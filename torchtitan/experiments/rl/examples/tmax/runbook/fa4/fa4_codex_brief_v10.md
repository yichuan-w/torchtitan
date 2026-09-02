# Task: FA4 backward on B300 (SM103) — round 10, look at the producer

## Read this first: two things changed

**1. Printing does not fix the hang.** Earlier rounds were built on the premise
that a pair of `cute.printf` calls straddling the compute warp's LSE wait turned
the hang into a pass. That premise is retracted — re-running the preserved
"passing" build hangs like everything else, and its three sources were a
disproved claim, an unverified self-report, and a monitor matching text from its
own brief.

The useful consequence: **`cute.printf` is now a safe diagnostic.** It does not
change the outcome, so instrument freely. That was not established before, and
it is what makes this round possible.

**2. The failure is deterministic.** The unmodified kernel hangs in 10 of 10
runs on the smallest configuration. Any single run is now a trustworthy verdict.

## The question nobody has asked

The MMA warp blocks in `pipeline_dO.consumer_wait` after completing Q and dS for
iteration 2. Nine rounds have all asked what is wrong at the *consumer*. Nobody
has checked the obvious other half:

**Does the load warp ever produce dO for that iteration?**

A consumer that waits forever usually means the producer stopped. Find out where
the load warp actually is when the MMA warp is stuck. Three possibilities, and
the trace will say which:

- the load warp never reaches the dO `producer_acquire` for that iteration — then
  find what *it* is blocked on, and follow that chain back;
- it reaches `producer_acquire` and blocks there — then the consumer side never
  released a stage, and the two are deadlocked on each other;
- it completes `producer_commit` and the consumer still does not see it — then
  the barrier's arrival accounting is wrong, and the interesting quantity is the
  expected transaction count versus what actually arrives.

## Method

Instrument all three warps in one build so their progress is directly
comparable, printing the iteration index and the pipeline state at each point:

- **load warp**: entry to each loop iteration, dO `producer_acquire` before and
  after, the TMA issue, `producer_commit` before and after, and the same for
  dPsum and Q.
- **MMA warp**: entry to each iteration, and before/after every `consumer_wait`
  it performs, naming which pipeline.
- **compute warp**: entry to each iteration and its LSE and dPsum waits and
  releases.

Guard each print to one lane of the warp in question so the output stays
readable, run the smallest configuration under `timeout 180`, and read the last
lines: the last iteration each warp reached is the answer.

Then state, in one sentence, which warp stopped first and what it was waiting
for. Follow that chain until you reach something that is waiting for nothing —
that is the bug.

## Established, do not re-derive

- Deterministic hang, 10/10, unmodified kernel, smallest configuration.
- Not version (`b26`, source `fb02fc8`), not the PyTorch shim, not 2-CTA, not
  compile time, not contention, not shape (D=64/128, S=256/512/2048, causal and
  not — all hang).
- These change nothing, all still hang: warp and block barriers around the LSE
  wait (predicated and not), `fence_acq_rel_cta`, inline asm with a memory
  clobber, `clock64` delays at seven sites, `defer_sync=False` on LSE and on
  LSE+dPsum, enabling the commented-out `cta_layout_vmnk` on LSE and LSE+dPsum,
  ptxas `opt-level` 0-3, forcing the CUDA 13.4 ptxas over Triton's 12.9,
  one-bit phase parity, dO/dPsum stages 1→2, a dedicated dO consumer handle.
- MLIR is identical between instrumented and plain builds apart from the prints.
- The LSE and dPsum pipelines are the only two built as `PipelineTmaAsync` with
  `defer_sync=True` and `cta_layout_vmnk` commented out; Q and dO are
  `PipelineTmaUmma` and pass it.

## Constraints

Do not remove, weaken or skip synchronization as a fix; adding it is fine when
you can say what ordering it enforces. Do not edit `fa4_bwd_acceptance.py`.
GPU 6/7 only, `timeout 180` per run, `pkill -9 -f` after a timeout. Copy files
to `fa4-fix/orig/` before first edit, diffs to `fa4-fix/patches/`. Restore the
installed tree when done and say so.

**Claiming a fix requires the acceptance script to pass with no `cute.printf`
and no probe in the modified files, and it will be re-run independently.**

## Report

The three-warp trace, which warp stopped first and on what, and the chain back
to whatever is waiting for nothing.
