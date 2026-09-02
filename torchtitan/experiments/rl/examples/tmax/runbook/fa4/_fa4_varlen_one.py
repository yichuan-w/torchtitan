#!/usr/bin/env python3
"""One varlen forward+backward through FA4, for the DSL compatibility sweep.

The dense SDPA path and the varlen path are different kernels, and the training
loop uses varlen. A sweep that only covers dense says nothing about whether
training can run — which is what a stuck 48-minute run made concrete: dense
passes on 4.6.2 while varlen stops inside `_bwd_postprocess_convert`, with the
same stack 75 seconds apart.

Exits 0 on success. A hang has to be caught from outside with a timeout, since
the call never returns.

Usage: _fa4_varlen_one.py [nseq] [seqlen] [nheads] [headdim]
"""
from __future__ import annotations

import sys

import torch
import torch.nn.attention as A
from torch.nn.attention.varlen import varlen_attn

NSEQ = int(sys.argv[1]) if len(sys.argv) > 1 else 4
SEQ = int(sys.argv[2]) if len(sys.argv) > 2 else 256
NH = int(sys.argv[3]) if len(sys.argv) > 3 else 4
HD = int(sys.argv[4]) if len(sys.argv) > 4 else 64

A.activate_flash_attention_impl("FA4")
assert A.current_flash_attention_impl() == "FA4", "FA4 impl not active"

torch.manual_seed(0)
total = NSEQ * SEQ
cu = torch.arange(0, total + 1, SEQ, device="cuda", dtype=torch.int32)

q, k, v = (
    torch.randn(total, NH, HD, device="cuda", dtype=torch.bfloat16,
                requires_grad=True)
    for _ in range(3)
)

print(f"varlen {NSEQ}x{SEQ} heads={NH} dim={HD}", flush=True)
out = varlen_attn(q, k, v, cu, cu, SEQ, SEQ)
print("forward done", flush=True)
out.sum().backward()
torch.cuda.synchronize()
print("backward done", flush=True)

ok = all(t.grad is not None and torch.isfinite(t.grad).all() for t in (q, k, v))
print(f"RESULT ok={ok}")
sys.exit(0 if ok else 1)
