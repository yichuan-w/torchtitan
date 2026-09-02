# Task: minimal CuTe DSL test for one tensor-memory copy on B300

## Context

We maintain a PyTorch training stack that uses FlashAttention-4's CuTe DSL
kernels. On our B300 (SM103) machines the backward pass does not complete: the
kernel launches and never returns. Forward works and is fast. This is ordinary
performance-engineering triage on a library we depend on, and we are trying to
narrow it enough to file a useful upstream report.

Instrumenting the kernel's three warps with `cute.printf` showed the load warp
completes its `producer_acquire` for the dO stage and then stops while issuing
the dO tensor copy — the log lines after the issue never appear. The equivalent
copy for Q, in the same warp and the same iteration, completes normally.
Comparing the two copies' tensor descriptors field by field found no
malformation; the one structural difference is that dO's tensor map uses a
mode-0 contiguous layout, which the transposition in `P.T @ dO` requires, while
Q's does not.

## What to write

A small standalone CuTe DSL program — no attention, no pipelines, no extra warps
— that issues a single bulk tensor copy from global to shared memory and waits
on its barrier. Then run it across variants and record whether each completes or
times out:

1. a descriptor shaped like dO's (mode-0 contiguous, transposed layout);
2. a descriptor shaped like Q's with the same element count, as a control that
   should complete;
3. the dO shape without the transposition, if expressible;
4. the dO shape at progressively smaller box dimensions;
5. if some size behaves differently from another, bisect to find where it
   changes.

## Why this shape of test

If the standalone copy also fails to complete, the problem is that one copy with
that descriptor shape, which is a small self-contained example anyone can run —
far more useful to the library authors than "the backward pass hangs". If it
completes cleanly, the descriptor is fine on its own and the interaction with
the surrounding kernel state is what matters, which redirects the search; say
which part of that state you would look at next and why.

## Already known, no need to re-check

The failure is deterministic: 10 of 10 runs on the unmodified library. It is not
the library version (two independent versions), not the PyTorch integration
layer (the library's own Python API behaves the same), not the 2-CTA path, not
compilation time, not GPU contention, not the tensor shapes. Adding warp-level
or block-level barriers around the earlier LSE wait, memory fences, timing
delays at seven points, `defer_sync=False`, enabling `cta_layout_vmnk`, changing
the compiler optimization level, and using the newer CUDA 13.4 toolchain instead
of 12.9 all leave the behaviour unchanged.

## Practical notes

Use GPU 6 or 7 only; the others are running other people's jobs. Put a
`timeout 180` on every run and `pkill -9 -f` your script after a timeout, since
a kernel that does not finish holds its GPU. Keep your work under
`/scratch/gpfs/TRIDAO/al9080/fa4-fix/`, copy any library file to `orig/` before
editing it, and restore the installed library byte-for-byte when you finish.

## Report

The program, the variant table with complete/timeout for each row, and what it
implies about where the problem lives.
