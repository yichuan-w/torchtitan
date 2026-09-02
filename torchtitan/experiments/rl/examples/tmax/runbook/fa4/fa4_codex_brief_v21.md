# Task: rebuild the test program on the library's pipeline classes

## Why the current one needs replacing

`tma_one_copy.py` writes its own producer/consumer coordination: it sets up
mbarriers directly, computes phase parities by hand, and wraps the waits in
`elect_one`. That is the most delicate part of this kind of kernel, and it is
also the part we are trying to learn about, so a mistake in the test program
produces the same symptom as the problem we want to study. It has produced two
misleading results, each of which stood for several rounds.

The most recent one: adding any block-wide synchronization after the loop — a
`NamedBarrier` at any id, or a plain `__syncthreads()` — leaves the loop waiting
mid-iteration, with the producer waiting for an empty buffer while the consumer
waits for a full one. Without that synchronization the same program finishes.
A plain mistake in the phase arithmetic would stop at the same iteration either
way, so this looks like a timing-sensitive coordination fault that small changes
to instruction scheduling make visible. The tensor-memory result from the round
before was the same fault, made visible by the barrier in the allocation's
release sequence instead.

Both reproduce on H100, so neither tells us anything about SM103.

## What to build

A test program whose coordination comes from the library rather than from us.

Use the pipeline classes the real backward kernel uses — `PipelineTmaAsync` in
`cutlass.pipeline`, along with whatever the SM100 backward builds for its
producer and consumer roles — instead of calling `mbarrier_init`,
`mbarrier_wait` and computing phases directly. Follow the backward source and
mirror its pipeline setup: the same stage count, the same producer and consumer
thread roles, the same barrier layout.

Keep everything else the same as before: one block, a loop of bulk tensor copies
feeding a consumer, and the descriptors, sizes and buffer counts that already
compile and run today.

## How results are judged

**A result counts only as a pair.** It has to stop on B300 and finish on H100,
same program, same arguments, same library version. A B300 stall on its own is
not evidence — that is how the last two rounds went wrong.

The H100 machine is not reachable from here, so it is run separately. Get the
program working and narrowing on B300, and report each configuration you want
checked as an exact command line. Anything that also stalls on H100 gets
dropped rather than explained.

Before narrowing anything, show the new program's starting point is sound: the
full configuration finishes on B300, repeatedly, with nothing added — and still
finishes with a `__syncthreads()` after the loop. If a post-loop sync still
leaves it waiting, the coordination is still wrong and nothing built on top of
it is worth running.

## Practical notes

GPU 6 or 7 only. `timeout 170` on every run. Work under
`/scratch/gpfs/TRIDAO/al9080/fa4-fix/`. Read the installed library freely, but
leave it as you found it; if you need to change a file there, keep the original
in `orig/` and put it back when done.

Write results to a file as you go, one line per run with the exact command and
the outcome, rather than reporting only at the end.

## Report

The new program, the evidence that its starting point is sound (how many repeats,
with and without a post-loop sync), and the list of configurations you want
checked on H100, as exact command lines. If the library's pipeline classes
cannot express something the real kernel does, say which and why — that gap is
worth knowing on its own.
