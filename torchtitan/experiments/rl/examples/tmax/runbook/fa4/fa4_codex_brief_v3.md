# Task: fix the FA4 backward hang on B300 (SM103) — third attempt

## Goal

```
cd /scratch/gpfs/TRIDAO/al9080/fa4-fix
CUDA_VISIBLE_DEVICES=6 /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python fa4_bwd_acceptance.py
```

Done when it prints `ALL PASS (12/12 configurations)`. Read that script first —
it is the specification, and it checks gradient accuracy as well as termination,
so a kernel that returns quickly with wrong numbers fails it.

## The sharpest fact available, and where to start

Tracing the smallest hanging configuration showed the MMA warp completing **Q**
and **dS** for iteration 2, then blocking forever in `pipeline_dO.consumer_wait`.

So the Q and dS pipelines advance on this hardware and the dO pipeline does not.
That is the whole question: **what is different about how the dO pipeline is
constructed, filled, or consumed, compared with the ones that work?** Compare
them line by line — stage count, producer/consumer masks, `cta_group`, the
mbarrier each uses, who arrives and how many arrivals each wait expects, and
where each producer's acquire sits relative to the loop. The answer is very
likely an asymmetry between dO and its neighbours rather than anything specific
to SM103 semantics.

## Everything already established — do not spend a run re-deriving any of it

* **Not a version regression.** PyPI `flash_attn_4 4.0.0b26` and the older
  source checkout in `fa4-fix/upstream/` (commit `fb02fc8`, 322 lines different
  in `flash_bwd_sm100.py`) both hang on every configuration. Do not bisect
  versions.
* **Not the PyTorch integration.** `flash_attn.cute.interface.flash_attn_func`
  hangs identically to the SDPA path.
* **Not 2-CTA.** `FA_DISABLE_2CTA=1` changes nothing, and head_dim=64 hangs
  although it never enables 2-CTA.
* **Not contention or compilation.** Reproduced on an idle GPU; the wedged
  process sits at 0% CPU while that GPU reports 100% utilization, so a kernel is
  resident and not finishing. Forward compiles in under 4s and works.
* **Not shape-dependent.** D=64 and D=128, S=256/512/2048, causal and not, all
  hang — including the smallest.
* **Already tried and ruled out:** normalizing `PipelineStateSimple.phase` to
  one-bit parity (with forced recompilation); raising the SM103 dO/dPsum stage
  count from one to two.
* Hardware: B300 SXM6, capability (10, 3) = SM103. Forward carries explicit
  SM103 handling (`flash_fwd_sm100.py` `is_sm103`, `softmax.py` note about
  `tcgen05.ld.red`); the four backward files carry none.

## Hard constraint, unchanged

**Do not remove, weaken, or conditionally skip any synchronization primitive** —
`producer_tail`, barriers, named barriers, fences, `cp_async_wait`, mbarrier
waits. An earlier attempt passed the small configurations by skipping
`producer_tail` on SM103; it was rejected and reverted, because that trades a
hang for a race the passing sizes are too small to expose, and the field failure
mode becomes silently wrong gradients in a training run. If you conclude the
correct fix requires touching one, stop and explain why instead.

Also: do not edit `fa4_bwd_acceptance.py`, and do not route SM103 to another
backend or disable FA4 backward.

## Method that is working — keep using it

Instrument, do not guess. The iteration-2 stall above came from `cute.printf`
tracing, and it is worth far more than another speculative patch. The trace to
build next, in one run so the three sides are directly comparable:

* the load warp's acquire/commit points for dO and dPsum,
* the compute warps' waits and releases on LSE and dPsum,
* the MMA warp's dO wait,

each printing its iteration index and the phase value it is waiting on. Then
find the first iteration where two sides disagree about the phase, and work back
to the arithmetic that produced the disagreement. Remove all tracing before the
final acceptance run.

## Rules of engagement

* GPU 6 and 7 only; other GPUs belong to other people's jobs.
* Every run gets `timeout 180`, and after a timeout `pkill -9 -f` your script —
  a hung kernel holds a GPU and a core indefinitely.
* Copy a file to `fa4-fix/orig/` before first editing it; write unified diffs to
  `fa4-fix/patches/`. The installed tree is currently pristine b26 and matches
  `installed-b26-backup/` byte for byte — keep it that way unless a change is
  deliberate.

## If you still cannot fix it

Say so plainly and hand over the trace: which iteration, which phase values,
which side was ahead. A precise disagreement is a result, and it is what the
maintainer would need anyway. Do not paper over it with a patch that merely
stops the hang.
