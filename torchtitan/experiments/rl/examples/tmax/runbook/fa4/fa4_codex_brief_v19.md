# Task: make the two-warp copy test correct, using an H100 as the oracle

## What happened and why this round exists

The test program was extended to split producing and consuming across two
warps. It times out on the B300 — the first timeout in the whole reduction —
which looked like a result.

It is not one yet. **The same program, same arguments, also times out on an
H100.** Two different architectures failing identically means the likeliest
explanation is that the program's own producer/consumer handshake is wrong, not
that either machine is. A handshake with a wrong phase or a wrong first-use
guard hangs at exactly the observed place: iteration 0 completes, the first
reuse does not.

So nothing can be concluded from the two-warp tests until the program is known
to be correct.

## The task

**Make the program complete on the H100.** That is the whole job this round.

Environment there:

```
ssh flow-matic-andy
cd /work/tianxia
CUDA_VISIBLE_DEVICES=0 ./cute-h100/bin/python tma_one_copy.py do_t --copies 1 --warps 2 --iterations 8
```

`torch 2.11.0+cu128`, `nvidia-cutlass-dsl==4.6.0` — the same DSL version as the
B300 box, so the API matches. Exit 124 means it timed out; 0 with a
`HOST_COMPLETE` line means it finished.

Work until these all complete on the H100:

- `--copies 1 --warps 2 --iterations 8`
- `--copies 3 --warps 2 --iterations 64`
- `--copies 3 --warps 3 --iterations 64`
- `--copies 3 --warps 2 --iterations 64 --do-buffers 2`

## Where to look first

The current shape is: barriers are initialized with arrival count 1 and none is
pre-signalled; each resource has a full barrier and an empty barrier; the
producer's wait on the empty barrier is skipped by a hardcoded `if it >= 2`,
which is the right guard only for a two-buffered resource; phases are computed
as `(it // buffers) % 2` and waits use `phase` for full and `1 - phase` for
empty.

Check that combination carefully for resources with one buffer versus two, and
for the first pass through each buffer when nothing has yet released it. State
what was wrong once you know.

## After it is correct

Do not change anything else, and run the same four configurations on the B300:

```
ssh della-tridao
cd /scratch/gpfs/TRIDAO/al9080/fa4-fix
CUDA_VISIBLE_DEVICES=7 /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python tma_one_copy.py ...
```

A configuration that completes on the H100 and times out on the B300 is a real
finding. One that fails on both is still the program's fault. Report the
side-by-side table.

## Practical notes

`timeout 180` on every run; clean up leftover processes. On the B300 use GPU 6
or 7 only. Do not modify the installed FlashAttention library — this round does
not involve it, and its source is not in this directory.

## Report

What the handshake bug was, the H100 table, and the B300 table beside it.
