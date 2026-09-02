#!/usr/bin/env python3
"""Varlen backward with the training context added back one piece at a time.

If the standalone varlen case passes on the DSL version where training stops,
then the DSL version is not what distinguishes them and the training context is:
longer sequences, the model's head shape, and a compiled graph — the RL recipe
sets `CompileConfig(enable=True, backend="aot_eager")`, so the backward runs
through aot_autograd rather than being called directly.

Each flag adds one of those, so the first one that stops returning is the answer
rather than a guess about which of them mattered.

Usage:
  _fa4_varlen_ctx.py [--seq N] [--nseq N] [--heads N] [--dim N] [--compile]
"""
from __future__ import annotations

import argparse

import torch
import torch.nn.attention as A
from torch.nn.attention.varlen import varlen_attn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nseq", type=int, default=4)
    ap.add_argument("--seq", type=int, default=256)
    ap.add_argument("--heads", type=int, default=4)
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--compile", action="store_true",
                    help="run through aot_eager, as the RL recipe does")
    ap.add_argument("--kv-heads", type=int, default=None,
                    help="fewer kv heads than query heads, i.e. GQA — which the "
                         "model uses and the first ladder left out entirely")
    ap.add_argument("--ragged", action="store_true",
                    help="segments of differing length, as real batches have; "
                         "every rung so far used equal ones")
    ap.add_argument("--iters", type=int, default=1,
                    help="repeat forward+backward; training calls this once per "
                         "layer per step, and a single call may not be enough "
                         "to reach whatever state the hang needs")
    args = ap.parse_args()

    A.activate_flash_attention_impl("FA4")
    assert A.current_flash_attention_impl() == "FA4", "FA4 impl not active"

    torch.manual_seed(0)
    if args.ragged:
        # Lengths that differ but still sum to the same total, so the only thing
        # that changes against the equal-length rungs is the segmentation.
        base = args.seq
        lengths = [base // 2, base, base * 2, args.nseq * base - (base // 2 + base + base * 2)]
        lengths = [n for n in lengths if n > 0]
    else:
        lengths = [args.seq] * args.nseq
    total = sum(lengths)
    offsets, acc = [0], 0
    for n in lengths:
        acc += n
        offsets.append(acc)
    cu = torch.tensor(offsets, device="cuda", dtype=torch.int32)
    max_len = max(lengths)
    kvh = args.kv_heads or args.heads
    q = torch.randn(total, args.heads, args.dim, device="cuda",
                    dtype=torch.bfloat16, requires_grad=True)
    k, v = (
        torch.randn(total, kvh, args.dim, device="cuda",
                    dtype=torch.bfloat16, requires_grad=True)
        for _ in range(2)
    )

    def run(q, k, v):
        return varlen_attn(q, k, v, cu, cu, max_len, max_len,
                           enable_gqa=kvh != args.heads)

    fn = torch.compile(run, backend="aot_eager") if args.compile else run

    label = (f"segments={lengths} heads={args.heads} kv_heads={kvh} "
             f"dim={args.dim} compile={args.compile} iters={args.iters}")
    print(label, flush=True)
    for it in range(args.iters):
        for t in (q, k, v):
            t.grad = None
        out = fn(q, k, v)
        out.sum().backward()
        torch.cuda.synchronize()
        print(f"iter {it} done", flush=True)
    ok = all(t.grad is not None and torch.isfinite(t.grad).all() for t in (q, k, v))
    print(f"RESULT ok={ok}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
