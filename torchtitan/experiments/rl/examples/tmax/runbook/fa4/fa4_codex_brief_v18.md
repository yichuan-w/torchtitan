# Task: split the small copy test across warps

## Scope

Work only on `tma_one_copy.py` in this directory. There is no library source
here to consult and you do not need any — everything required is below. Do not
search for or open the installed library.

## Where the reduction stands

The test program builds up from one block, one thread, and nothing but copies.
Everything below completes:

| test | result |
|---|---|
| one copy, dO-shaped tile, 128×128 down to 16×16 | completes |
| Q-shaped control; dO shape with the transpose removed | completes |
| two copies (Q, dO); three copies (Q, dO, LSE), separate waits | completes |
| exact buffer arrangement, single-buffered dO, 4 / 16 / 64 iterations | completes |
| that arrangement at 64- and 128-byte alignment | completes |

Reducing alignment to 16 or 32 bytes crashes with `misaligned address`. That is
a crash, not the failure being chased, and only means the variant is invalid.

**Only a timeout counts.** Record crashes, revert them, keep going.

## What is left, and why this one first

Every test so far has run in **one warp**. The real loop splits the work: one
warp issues the copies, others wait on them and consume. The observed stall is a
producer that has already passed its turn-taking step and then does not finish
issuing — a situation that needs a separate consumer to exist at all.

So:

1. **Two warps.** Warp 0 issues the three copies each iteration and signals;
   warp 1 waits on them and signals back that the buffers are free. Keep the
   arrangement from the last round: Q two buffers, dO one buffer, LSE two
   buffers, dPsum one buffer, wait objects contiguous ahead of the buffers in
   that order, 128-byte aligned, 64 iterations. The single-buffered dO now
   genuinely forces warp 0 to wait for warp 1 between iterations.
2. If that completes, **three warps**: add one that only waits on dO and does
   nothing else, so two consumers share one producer.
3. If that completes, add the **pre-loop setup** one piece at a time: first the
   tensor-memory allocation the kernel performs before its loop, then the
   cluster launch configuration.

Within any step that times out, narrow: which warp stops, at which iteration,
which wait object it is on. `cute.printf` is safe — it does not change the
outcome — so print per-warp progress freely.

## Numbers, unchanged

Smallest failing configuration (batch 2, heads 4, sequence 256, head dim 64,
non-causal, 2-CTA off):

- Q: 2 buffers, 4 wait objects; dO: 1 buffer, 2 wait objects;
  LSE: 2 buffers, 4 wait objects; dPsum: 1 buffer, 2 wait objects
- tile height 128; Q and dO tiles 128×64 bfloat16 = 16384 bytes each
- LSE and dPsum transfers 128 floats = 512 bytes each
- wait objects contiguous, 8-byte aligned, ordered Q, dO, LSE, dPsum, before the
  buffers; buffers contiguous, aligned, same order

## Settled, do not recheck

Deterministic, 10 of 10 runs of the real thing. Not the library version (two of
them), not the PyTorch layer, not the 2-CTA path, not compile time, not GPU
sharing, not tensor sizes, not the tile shape or transpose, not the copy count,
not buffer reuse, not the buffer layout or alignment. Unchanged by extra
barriers or fences, delays at seven points, `defer_sync=False`,
`cta_layout_vmnk`, optimization levels 0-3, the CUDA 13.4 toolchain, or
diagnostic printing.

## Practical notes

GPU 6 or 7 only, `timeout 180` per run, clean up leftover processes afterwards.

## Report

Change versus completes / times out / crashes, the first timeout, and which warp
was where when it stopped.
