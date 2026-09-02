# Task: keep growing the small copy test — layer 3 is clear too

## Do not read the library source. Everything needed is below.

## Correction to the stopping rule

Last round stopped at "alignment 16 bytes fails". That is a **crash** — CUDA
reports `misaligned address` — and it means the test program was configured
below what the hardware accepts. It is not the failure being chased.

**The failure being chased is a hang: the program stops producing output and the
timeout kills it.** A crash with a CUDA error message is a different thing and
means the variant is invalid; note it, revert it, and keep going. Only a timeout
counts as the flip you are looking for.

## Where the reduction stands

Everything below completes:

| test | result |
|---|---|
| one copy, dO-shaped tile, 128×128 down to 16×16 | completes |
| one copy, Q-shaped control; dO shape without transpose | completes |
| two copies (Q, dO) and three copies (Q, dO, LSE), separate waits | completes |
| exact buffer arrangement, single-buffered dO, 4 / 16 / 64 iterations | completes |
| that arrangement at 64- and 128-byte buffer alignment | completes |

So tile shape, copy count, the single-buffered dO, buffer reuse across many
iterations, and the buffer layout are all cleared.

## The arrangement, as numbers, unchanged from last round

Smallest failing configuration (batch 2, heads 4, sequence 256, head dim 64,
non-causal, 2-CTA off):

- Q: two buffers, 4 wait objects. dO: one buffer, 2 wait objects.
  LSE: two buffers, 4 wait objects. dPsum: one buffer, 2 wait objects.
- tile height 128; Q and dO tiles 128×64 bfloat16 = 16384 bytes each
- LSE and dPsum transfers 128 floats = 512 bytes each
- wait objects contiguous, 8-byte aligned, ordered Q, dO, LSE, dPsum, ahead of
  the buffers; buffers contiguous and aligned, same order

## Remaining layers, in order

4. **Wait-object placement**: put them after the buffers instead of before; then
   interleave them with the buffers rather than grouping them.
5. **Double-buffer dO** (2 buffers, 4 wait objects) and keep everything else, as
   a control — it should still complete, and if it does not, the asymmetry is
   the answer.
6. **Two warps instead of one**, one issuing the copies and one waiting on them,
   which is how the real loop divides the work. Then three warps, adding one
   that only consumes.
7. **Pre-loop setup**, one piece at a time: the tensor-memory allocation, then
   the cluster launch configuration.

Layer 6 is the most promising of these, because every test so far has run in a
single warp while the real kernel splits producing and consuming across warps,
and the stall shows a producer that got past its turn-taking step.

Run after each change; record completes, times out, or crashes with a message.
Stop only on a timeout.

## Settled already

Deterministic, 10 of 10. Not the library version (two of them), not the PyTorch
layer, not the 2-CTA path, not compile time, not GPU sharing, not tensor sizes.
Unchanged by extra barriers or fences, delays at seven points, `defer_sync=False`,
`cta_layout_vmnk`, optimization levels 0-3, the CUDA 13.4 toolchain. Diagnostic
printing does not change the outcome. Raising the dO buffer count inside the
real library did not help on its own.

## Practical notes

GPU 6 or 7 only, `timeout 180` per run, clean up leftovers. Work under
`/scratch/gpfs/TRIDAO/al9080/fa4-fix/`. Do not modify the installed library.

## Report

Change versus completes / times out / crashes, the first timeout, and the
narrowing inside it.
