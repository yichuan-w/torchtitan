# Task: make FlashAttention-4 backward work on B300 (SM103) — second attempt

## The goal, unchanged

```
cd /scratch/gpfs/TRIDAO/al9080/fa4-fix
CUDA_VISIBLE_DEVICES=6 /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python fa4_bwd_acceptance.py
```

Done when it prints `ALL PASS (12/12 configurations)`. Read the script first.

## Read this before touching anything: why the first attempt was rejected

A previous run reached PASS on `S=256` and `S=512` by making `flash_bwd_sm100.py`
skip `pipeline_Q.producer_tail(...)`, `pipeline_LSE.producer_tail(...)` and the
`should_load_dO` drain when the architecture is SM103. That change was rejected
and has been reverted.

It was rejected for a reason that matters more than the passing configs.
`producer_tail` is the drain that keeps a producer from exiting while consumers
are still reading its shared-memory buffers. Removing it does not make the race
go away; it makes the *wait* go away, and whether that corrupts anything then
depends on timing the acceptance test does not control. Small shapes passing is
not evidence of safety — it is evidence that the race window did not open at
that size. The failure this produces in the field is silently wrong gradients,
which an RL run would train on for days before the loss curve showed anything.

**Hard constraint: do not remove, weaken, or conditionally skip any
synchronization primitive** — `producer_tail`, `barrier`, `named_barrier`,
`fence`, `cp_async_wait`, mbarrier waits. If the correct fix genuinely requires
changing one, stop and explain why rather than doing it.

## What that failed attempt nevertheless established

Skipping the drain made `S=256` and `S=512` pass while `S=2048` still hung.
Read that as a measurement, not a workaround: **the drain is where the hang
becomes visible, not where it originates**, and something that scales with
sequence length — the number of tiles, hence the number of pipeline iterations —
decides whether it hangs at all.

That shape of bug is usually arithmetic, not structure: a phase/parity
computation, a stage count, or a barrier arrival count that is right for one
number of iterations and wrong for another. Look for a quantity computed from
the number of stages or the tile count, and check it against how many arrivals
the consumers actually make on SM103. `pipeline.py` holds the phase and stage
bookkeeping; `flash_bwd_sm100.py` decides how many iterations run.

## What is already known and must not be re-derived

* GPU: B300 SXM6, capability (10, 3) = SM103. `arch // 10 == 10` dispatches it
  onto the SM100 kernels.
* Forward works and adapts to SM103 explicitly (`flash_fwd_sm100.py:235`
  `is_sm103`, and `softmax.py:306` "Row max already reduced in hardware (SM103
  tcgen05.ld.red)"). The four backward files contain no SM103 handling at all.
* Upstream issue #2376: the maintainer states FA4 is currently tested on
  Sm90–Sm100, and other architectures "may work or need some small fix (or
  sometimes major implementation)".
* The hang is on the GPU: the wedged process sits at 0% CPU while its otherwise
  idle GPU reports 100% utilization.
* It is not PyTorch's shim — `flash_attn.cute.interface.flash_attn_func` hangs
  the same way. It is not 2-CTA — `FA_DISABLE_2CTA=1` does not help and
  head_dim=64 never enables it. `4.0.0b26` is the newest release.

## Suggested first move

Instrument rather than guess. Add temporary `cute.printf` tracing of the
iteration index and the phase each producer/consumer is waiting on, run the
smallest hanging config (`S=2048, D=64, causal=0`), and find the iteration at
which the two sides disagree. Compare that iteration number against the stage
count. Remove the tracing before the final acceptance run.

## Rules of engagement

* GPU 6 and 7 only. Other GPUs run other people's jobs.
* Every run gets `timeout 180`; after a timeout, `pkill -9 -f` your script. A
  hung kernel holds its GPU and a core indefinitely.
* Do not edit `fa4_bwd_acceptance.py`. If you believe a threshold in it is
  wrong, stop and say so.
* Copy any file to `/scratch/gpfs/TRIDAO/al9080/fa4-fix/orig/` before first
  editing it, and write unified diffs into `.../fa4-fix/patches/`.

## Report at the end

The mechanism in one paragraph, the diffs, the acceptance output, and what you
tried that did not work. If you cannot fix it within the constraints, say so
plainly and describe what evidence you would need — that is a useful result, and
far better than a patch that passes by deleting a wait.
