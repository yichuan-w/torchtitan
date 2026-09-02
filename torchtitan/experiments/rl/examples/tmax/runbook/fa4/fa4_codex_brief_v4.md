# Task: FA4 backward hang on B300 (SM103) — fourth attempt

## Goal

```
cd /scratch/gpfs/TRIDAO/al9080/fa4-fix
CUDA_VISIBLE_DEVICES=6 /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python fa4_bwd_acceptance.py
```

`ALL PASS (12/12 configurations)`, **with no `cute.printf` anywhere in the
modified files at the time of that run**. This is checked: the last attempt
reached ALL PASS with tracing still compiled in, the tracing was stripped
afterwards, and all 12 configurations hung again. Verify it yourself before
claiming success — `grep -rn "printf" ` your changed files, then run.

## The new fact, and why it decides your method

Adding per-iteration `cute.printf` in the load warp made every configuration
pass. Removing those prints — and nothing else — made every configuration hang
again. **The deadlock is timing-sensitive.**

That rules out a great deal. A miscomputed constant, an off-by-one stage count,
a wrong phase literal — none of those would be fixed by making one warp slower.
What remains is a genuine race: two participants whose required ordering is not
enforced, which happens to come out right when the load warp is delayed.

Your job this round is to identify **which two participants and which ordering**.
Fixing it is secondary to naming it precisely.

## Method: bisect with delay, not with print

`cute.printf` is a blunt instrument — it changes timing *and* serializes I/O.
Replace it with something whose only effect is delay (a spin on
`cute.arch.nanosleep` if available in this DSL version, otherwise a bounded
dummy loop the compiler cannot elide), and then bisect:

1. Insert one delay at a single site, run, record pass or hang.
2. Move it. Narrow to the smallest set of sites where a delay makes the
   difference.
3. The site that matters tells you which participant must not run ahead. Read
   the code on both sides of that point and work out what ordering the original
   assumed but did not enforce.

Report the answer as a sentence of the form: "X may reach A before Y reaches B,
and nothing prevents it."

## What has already been established — do not re-derive

* Not a version regression: PyPI `4.0.0b26` and source checkout `fb02fc8` both
  hang on every configuration.
* Not the PyTorch shim: `flash_attn.cute.interface.flash_attn_func` hangs the
  same way.
* Not 2-CTA (`FA_DISABLE_2CTA=1` changes nothing; head_dim=64 never enables it),
  not compilation (forward compiles in under 4s), not contention (idle GPU),
  not shape (D=64/128, S=256/512/2048, causal and not — all hang).
* Stall point: the MMA warp completes Q and dS for iteration 2, then blocks
  forever in `pipeline_dO.consumer_wait`. The Q and dS pipelines advance; dO
  does not.
* Tried and insufficient: normalizing `PipelineStateSimple.phase` to one-bit
  parity; raising the SM103 dO/dPsum stage count from 1 to 2; giving
  `pipeline_dO` its own consumer handle with explicit `wait_and_advance()` /
  `release()` in the MMA loop (this last one is in
  `fa4-fix/with-trace-flash_bwd_sm100.py` along with the tracing — it passes
  only while the prints are present).
* Hardware: B300 SXM6, capability (10, 3) = SM103. Forward has explicit SM103
  handling; the four backward files have none.

## Constraints

**Do not remove, weaken, or conditionally skip any synchronization primitive.**
A hang traded for a race is a worse bug: the field failure becomes silently
wrong gradients in a training run rather than an obvious stall.

**Adding synchronization is allowed and may well be the answer.** If the race is
real, something is missing, not surplus. An added wait, barrier, or fence that
is justified by the ordering you identified is exactly the kind of fix wanted —
say which ordering it enforces and why it is needed on SM103 but not SM100.

Do not edit `fa4_bwd_acceptance.py`. Do not route SM103 elsewhere or disable FA4
backward. The installed tree is pristine b26 again and matches
`installed-b26-backup/` byte for byte.

## Rules of engagement

GPU 6 and 7 only. Every run gets `timeout 180`, and `pkill -9 -f` after any
timeout — a wedged kernel holds a GPU and a core indefinitely. Copy files to
`fa4-fix/orig/` before first edit; unified diffs into `fa4-fix/patches/`.

## If you cannot fix it

Then deliver the sentence: which participant, which point, which ordering is
unenforced, and the delay-bisection evidence behind it. That is a result worth
having on its own, and it is what someone who wrote this pipeline would need in
order to fix it in minutes.
