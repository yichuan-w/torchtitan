# Task: make FlashAttention-4 backward work on B300 (SM103)

## The goal, stated as a command

```
cd /scratch/gpfs/TRIDAO/al9080/fa4-fix
CUDA_VISIBLE_DEVICES=6 /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python fa4_bwd_acceptance.py
```

You are done when that prints `ALL PASS (12/12 configurations)` and exits 0.
Read the script before you start — it is the specification, and it is short.

It checks two things per configuration. First that the call returns at all,
each config in its own process behind a hard timeout. Second that the gradients
are as accurate as PyTorch's own bf16 attention: it computes a float32 math
reference, measures how far bf16 mem-efficient lands from it, and requires FA4
to land no more than twice as far. That second check exists because a kernel
that returns fast with quietly wrong gradients is worse than one that hangs —
an RL run would train on them for days before the loss curve showed anything.

## What is broken

FA4 forward works on this machine and is roughly 9x faster than mem-efficient.
FA4 **backward hangs**: the kernel launches and never returns.

## What is already ruled out — do not re-derive these

* **Not a compile-time problem.** The forward kernel compiles in 3.8s. The
  Python frame sits at `flash_attn/cute/interface.py` `_bwd_postprocess_convert`,
  on the line that *invokes* the compiled kernel; compilation happened on the
  line above.
* **It is on the GPU, not the CPU.** With the process wedged on an otherwise
  idle GPU, `nvidia-smi` shows that GPU at 100% utilization while the process
  sits at 0% CPU. A kernel is running and not terminating.
* **Not PyTorch's integration.** Calling `flash_attn.cute.interface.flash_attn_func`
  directly, bypassing `torch/nn/attention/_fa4.py`, hangs identically.
* **Not 2-CTA.** `FA_DISABLE_2CTA=1` does not help, and head_dim=64 hangs even
  though that path never enables 2-CTA.
* **Not configuration-dependent.** head_dim 64 and 128, seqlen 256/512/2048,
  causal and non-causal all hang.
* **Not contention.** Reproduced on a completely idle GPU.
* **Not fixed by upgrading.** `flash_attn_4 4.0.0b26` is the newest release.

## The most probable cause

This GPU is **B300 SXM6, compute capability (10, 3) = SM103**, not B200/SM100.
`flash_attn/cute/interface.py` computes `arch = major*10 + minor = 103` and then
dispatches on `arch // 10 in [10, 11]`, so SM103 runs the **SM100** kernels.

The forward path knows the difference and adapts to it:

* `flash_fwd_sm100.py:235` — `is_sm103 = self.arch.is_family_of(Arch.sm_103f)`,
  used in tuning keys and to disable `ex2_emu`.
* `softmax.py:306` — "Row max already reduced in hardware (SM103 tcgen05.ld.red)".

The backward path does not: `flash_bwd_sm100.py`, `flash_bwd_postprocess.py`,
`flash_bwd_preprocess.py` and `flash_bwd.py` contain **zero** references to
sm103. Upstream confirms this shape of gap — in Dao-AILab/flash-attention issue
#2376 the maintainer writes that FA4 is currently tested on **Sm90–Sm100** and
that other architectures "may work or need some small fix (or sometimes major
implementation)".

So: find which SM103 hardware behaviour the backward kernels assume incorrectly,
and adapt them the way forward already does. Treat the above as the leading
hypothesis, not as fact — if the evidence takes you elsewhere, follow it, but
say so explicitly in your report.

## Constraints on what counts as a fix

* Do **not** route SM103 to a different backend, disable FA4 backward, or fall
  back to math/mem-efficient. The deliverable is a working SM103 backward
  kernel.
* Do **not** weaken, skip, or edit `fa4_bwd_acceptance.py`. If you believe a
  threshold in it is genuinely wrong, stop and say so rather than changing it.
* Forward must keep working; the acceptance script checks forward output by the
  same rule.

## Where things are

* Python: `/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python`
* Package under test: `/scratch/gpfs/TRIDAO/al9080/titan-rl/lib/python3.12/site-packages/flash_attn/cute/`
* Work here: `/scratch/gpfs/TRIDAO/al9080/fa4-fix/`
* Use **GPU 6 and 7 only** (`CUDA_VISIBLE_DEVICES=6`). The other GPUs are
  running other people's jobs — do not touch them.

## How to work without wedging the machine

Every run you launch must have a hard timeout (`timeout 180 ...`). A hung
kernel holds its GPU and one CPU core indefinitely; two such processes were
found running for 23 hours before being killed. After any run that times out,
`pkill -9 -f <your script>` before starting the next one.

The venv is shared and already carries local patches. Before editing any file
under `site-packages/flash_attn/`, copy it to
`/scratch/gpfs/TRIDAO/al9080/fa4-fix/orig/` if no copy is there yet, and after
each accepted change write a unified diff into
`/scratch/gpfs/TRIDAO/al9080/fa4-fix/patches/`. Those diffs are the deliverable
alongside the passing run — they are what gets proposed upstream.

## What to report at the end

The failing mechanism in one paragraph, the diffs you applied, the acceptance
script's final output, and anything you tried that did not work — that last part
saves the next person the same dead ends.
