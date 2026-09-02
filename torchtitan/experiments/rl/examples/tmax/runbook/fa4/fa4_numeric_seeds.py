#!/usr/bin/env python3
"""Run the one configuration that misses the accuracy bar, across seeds.

With the DSL the release pins, eleven of twelve acceptance configurations pass
and none hang. The twelfth, S=2048 D=128 non-causal, lands 9% past a bar that is
itself derived from a single random draw: the bar is twice the distance between
a bf16 baseline and an fp32 reference, and that distance moves with the seed. So
one draw cannot separate "FA4 is less accurate here" from "this draw put the
baseline unusually close to the reference".

The comparison is copied from the acceptance test rather than reinvented — same
baseline backend, same per-tensor bar with the same floor. An earlier version of
this script took the worst error and the worst bar across all four tensors and
paired them, which cannot fail: it puts the largest error against the largest
bar. Per-tensor is the whole point, since only one tensor is over.

Prints each tensor's ratio per seed. Ratios straddling 1.0 mean the bar is tight
rather than the kernel wrong; consistently above means the gap is real.
"""
from __future__ import annotations

import sys

import torch
import torch.nn.attention as A
from torch.nn.attention import sdpa_kernel, SDPBackend

B, H, S, D, CAUSAL = 2, 4, 2048, 128, False
SEEDS = [int(s) for s in sys.argv[1:]] or list(range(8))
NAMES = ["out", "dq", "dk", "dv"]

A.activate_flash_attention_impl("FA4")
assert A.current_flash_attention_impl() == "FA4", "FA4 impl not active"


def grads(base, backend, dtype):
    q, k, v = (t.detach().to(dtype).requires_grad_(True) for t in base)
    with sdpa_kernel(backend):
        o = torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=CAUSAL)
        o.sum().backward()
    torch.cuda.synchronize()
    return (o.detach().float(), q.grad.float(), k.grad.float(), v.grad.float())


print(f"config B{B} H{H} S{S} D{D} causal={int(CAUSAL)}")
failing_seeds = 0
for seed in SEEDS:
    torch.manual_seed(seed)
    base = [torch.randn(B, H, S, D, device="cuda", dtype=torch.bfloat16) for _ in range(3)]
    ref = grads(base, SDPBackend.MATH, torch.float32)
    bas = grads(base, SDPBackend.EFFICIENT_ATTENTION, torch.bfloat16)
    fa4 = grads(base, SDPBackend.FLASH_ATTENTION, torch.bfloat16)

    parts, seed_ok = [], True
    for n, r, b, f in zip(NAMES, ref, bas, fa4):
        err_base = (b - r).abs().max().item()
        err_fa4 = (f - r).abs().max().item()
        bar = max(2.0 * err_base, 1e-3)
        ratio = err_fa4 / bar
        seed_ok &= err_fa4 <= bar
        parts.append(f"{n}={ratio:.3f}{'!' if ratio > 1.0 else ''}")
    failing_seeds += not seed_ok
    print(f"  seed {seed:2d}  " + "  ".join(parts) + ("" if seed_ok else "   FAIL"))

print(f"{failing_seeds}/{len(SEEDS)} seeds fail the acceptance bar")
