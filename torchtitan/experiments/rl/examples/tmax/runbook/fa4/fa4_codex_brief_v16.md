# Task: keep growing the small copy test — layers 1 and 2 are clear

## Do not read the library source this round

Everything you need from it is written out below as plain numbers. Work only on
the standalone test program. Reading the library adds nothing here and has been
costing whole rounds.

## Where the reduction stands

`tma_one_copy.py` builds up from a program with one block, one thread and
nothing but the copies. Everything so far completes:

| test | result |
|---|---|
| one copy, dO-shaped tile (transposed, mode-0 contiguous), 128×128 | completes |
| the same at 128×96, 128×64, 128×32, 128×16, 96×96, 64×64, 32×32, 16×16 | completes |
| one copy, Q-shaped control | completes |
| one copy, dO shape without the transpose | completes |
| two copies, Q then dO, separate waits, the loop's order (`--copies 2`) | completes |
| three copies, Q then dO then LSE, separate waits (`--copies 3`) | completes |

So the tile shape is fine, no size threshold exists, and two or three copies in
flight are fine. The next difference between this program and the real loop is
how the buffers and wait objects are arranged.

## The arrangement to reproduce, as numbers

For the smallest failing configuration (batch 2, heads 4, sequence 256, head
dim 64, non-causal, 2-CTA off) the real loop uses:

- **Q: two buffers**, wait objects: 4 (two pairs)
- **dO: one buffer**, wait objects: 2 (one pair)   ← the asymmetry
- **LSE: two buffers**, wait objects: 4
- **dPsum: one buffer**, wait objects: 2
- tile height 128; Q and dO tiles are 128×64 of bfloat16 = 16384 bytes each
- LSE and dPsum transfers are 128 floats = 512 bytes each
- all wait objects live in one contiguous 8-byte-aligned array, in the order
  Q, dO, LSE, dPsum, ahead of the tile buffers
- the tile buffers are one contiguous allocation, each aligned, ordered
  Q buffers, then dO buffer, then LSE, then dPsum

Your three-copy test currently gives every tile its own fresh wait object, which
is equivalent to double-buffering all of them. **The real loop single-buffers
dO**: its producer cannot start the next dO transfer until the consumer releases
the one buffer, while Q's producer can run a tile ahead.

## Steps

3. Reproduce that arrangement: same buffer counts per tile, same wait-object
   counts, one contiguous wait-object array in that order, one contiguous buffer
   allocation in that order. Run several iterations of the loop, not one, so a
   single-buffer tile has to be reused.
4. If it still completes, vary within the arrangement: more iterations; the
   alignment of the allocations; putting the wait objects after the buffers
   instead of before; making dO double-buffered to see whether the asymmetry is
   what matters.
5. If it still completes, add the remaining pre-loop setup one piece at a time.

Run after every change and record completes or times out. Stop at the first
change where it stops completing, then narrow inside that change.

## Settled already, no need to recheck

Deterministic: 10 of 10 runs. Not the library version (two of them), not the
PyTorch layer, not the 2-CTA path, not compile time, not GPU sharing, not tensor
sizes. Unchanged by extra barriers or fences around the earlier wait, delays at
seven points, `defer_sync=False`, enabling `cta_layout_vmnk`, optimization
levels 0-3, or the CUDA 13.4 toolchain instead of 12.9. Diagnostic printing does
not change the outcome. Raising the dO buffer count from 1 to 2 inside the real
library did not help on its own.

## Practical notes

GPU 6 or 7 only. `timeout 180` on every run, and clean up any leftover process
afterwards. Work under `/scratch/gpfs/TRIDAO/al9080/fa4-fix/`. Do not modify the
installed library at all this round.

## Report

The table of change versus completes/times out, the first change that flips it,
and how you narrowed inside it.
