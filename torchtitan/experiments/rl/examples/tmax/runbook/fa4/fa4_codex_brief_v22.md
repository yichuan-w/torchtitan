# Task: independently check which cutlass-dsl versions FA4's backward runs on

## What is being checked

A claim is about to be sent upstream, and it should be verified by someone who
did not produce it. On a B300, with flash-attn-4 4.0.0b26:

1. The **dense** backward, through SDPA with the FA4 impl active, completes
   under nvidia-cutlass-dsl 4.6.0.dev0 and 4.6.2, and does not return under
   4.6.0 and 4.6.1.
2. The **varlen** backward, through `torch.nn.attention.varlen.varlen_attn`,
   does not return under 4.6.2 — it stops inside `_bwd_postprocess_convert`
   and stays there.

These are separate kernels, and the second one is what a training loop uses, so
the two have to be measured separately. Claim 2 rests on a single observation
(a stalled training run, same stack sampled 75 seconds apart after 48 minutes)
and has not been checked across DSL versions at all — if it holds on every
version, that changes what should be reported upstream.

## Build your own

Do not reuse the environment at `/scratch/gpfs/TRIDAO/al9080/fa4-correct-dsl`,
and do not reuse the scripts under `/scratch/gpfs/TRIDAO/al9080/fa4-fix/`. Make
your own virtualenv, write your own test, and reach your own numbers. If your
result differs from the claim, that is the useful outcome, so do not work
backwards from it.

Notes that will save you time, not instructions to follow blindly:

- `nvidia-cutlass-dsl==4.6.0.dev0` is a prerelease. uv excludes prereleases by
  default and reports "no version of ..." for it, which looks like the version
  does not exist; `--prerelease allow` installs it.
- flash-attn-4 pins the DSL, so installing it normally will pull the pinned one
  back. Install it without dependency resolution when you want to hold the DSL
  at a version you chose.
- torch 2.14.0.dev20260806+cu130 from the pytorch nightly index is a
  combination known to build here.

## What to measure

For each DSL version, run FA4's backward and record one of three outcomes,
kept distinct because they mean different things:

- **completes** — returns, with gradients
- **does not return** — still running at a hard timeout, killed
- **fails to compile** — raises before any kernel runs

The last one matters. One earlier run of 4.6.0 raised a compile-time type error
about `mO_cur` in `flash_fwd_sm100.py` rather than hanging, in a different
environment, and that discrepancy is unresolved. If you see a compile error
where the claim says it hangs, say so plainly and include the message — that
alone would block the report from being sent.

Run each version more than once. A single result per cell is not enough to send
anywhere, and the failure here is the kind that can depend on timing.

Measure **both** paths for every DSL version:

- dense: SDPA with the FA4 impl active, bf16, batch 2, 4 heads, sequence 256,
  head dim 64, non-causal, forward and backward.
- varlen: `torch.nn.attention.varlen.varlen_attn` with equal-length segments
  (4 sequences of 256 is enough), 4 heads, head dim 64, bf16, forward and
  backward.

Each run in its own process with a hard timeout, since a kernel that never
returns cannot be interrupted from inside the process running it.

## Practical notes

**GPU 6 only** — another sweep is using 7, and the rest of this shared box holds
other users' resident jobs. Check `nvidia-smi` first anyway. `timeout 180` on
every run. Work under a directory of your own. Leave the installed libraries
alone.

Write each run to a file as you go, one line per run with the exact command and
the outcome.

## Report

The table, how many repetitions per cell, and whether it agrees with the claim
above. If it does not agree, what you saw instead. If some cell is ambiguous,
say which and why rather than picking the tidier reading.
