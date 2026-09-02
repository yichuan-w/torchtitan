# Task: the last two layers — pre-loop setup

## Where the reduction stands

The test program now has a correct handshake, verified the only way that means
anything: it completes on an H100 as well as on the B300. The bug it had was
warp-divergent barrier polling — every lane calling the wait independently, so a
lane could sit in an old phase while another released the buffer.

With that fixed, these all pass on **both** machines:

| configuration | H100 | B300 |
|---|---|---|
| copies=1, warps=2, iterations=8 | pass | pass |
| copies=3, warps=2, iterations=64 | pass | pass |
| copies=3, warps=3, iterations=64 | pass | pass |
| copies=3, warps=2, iterations=64, do-buffers=2 | pass | pass |

Together with the earlier single-warp work, that clears: tile shape and
transpose, copy count, buffer counts and reuse, buffer layout and alignment,
iteration count, and the producer/consumer warp split.

**Keep using the H100 as the oracle.** Any change that fails on both machines is
the program's fault; only a change that passes on the H100 and fails on the
B300 says anything about the hardware.

## What is left

Two pieces of setup the real kernel performs before its loop, which the test
program still does not:

1. **The tensor-memory allocation.** The kernel allocates tensor memory and
   frees it at the end, and the loop runs inside that. Add it around the
   existing loop, changing nothing else.
2. **The cluster launch configuration.** The kernel launches with a cluster
   rather than a bare grid. Add that, changing nothing else.

Add them one at a time, in that order, and run the four configurations above on
both machines after each. If one of them makes the B300 fail while the H100
still passes, that is the finding, and then narrow inside it: allocation size,
where in the sequence it happens, cluster dimensions.

If both layers pass on both machines, say so plainly. That would mean the whole
loop body reproduces cleanly outside the library and the difference lies in
something the library does that this reduction has not captured — worth stating
as a result rather than dressed up.

## Practical notes

`timeout 180` per run; clean up leftover processes. B300: GPU 6 or 7 only, via
`della-tridao`, python at
`/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python`. H100: via
`ssh flow-matic-andy`, `cd /work/tianxia`, python at `./cute-h100/bin/python`,
`CUDA_VISIBLE_DEVICES=0`. Do not touch the installed FlashAttention library.

## Report

A table of layer versus H100 result versus B300 result, and the first divergence
if there is one.
