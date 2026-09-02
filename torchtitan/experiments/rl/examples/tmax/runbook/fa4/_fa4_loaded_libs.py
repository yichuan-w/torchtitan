#!/usr/bin/env python3
"""List the shared libraries a process actually has mapped after running FA4.

Package versions in the two environments now match on everything that looked
relevant, and one still hangs on grouped-query varlen backward while the other
does not. What pip reports is which wheels are present; what decides the kernel
is which `.so` the process ends up with, and one environment carries a full set
of cu12 CUDA libraries alongside a cu130 torch.

Runs a non-GQA forward and backward first, so the CUDA and compiler libraries
are loaded by the same path as the failing case, then prints what is mapped.

Usage: _fa4_loaded_libs.py
"""
from __future__ import annotations

import pathlib
import re

import torch
import torch.nn.attention as A
from torch.nn.attention.varlen import varlen_attn

A.activate_flash_attention_impl("FA4")

torch.manual_seed(0)
seq, nseq, nh, hd = 256, 2, 4, 64
total = seq * nseq
cu = torch.arange(0, total + 1, seq, device="cuda", dtype=torch.int32)
q, k, v = (
    torch.randn(total, nh, hd, device="cuda", dtype=torch.bfloat16,
                requires_grad=True)
    for _ in range(3)
)
out = varlen_attn(q, k, v, cu, cu, seq, seq)
out.sum().backward()
torch.cuda.synchronize()

libs = set()
for line in pathlib.Path("/proc/self/maps").read_text().splitlines():
    m = re.search(r"(/\S+\.so[\.\d]*)$", line)
    if m:
        libs.add(m.group(1))

interesting = sorted(
    p for p in libs
    if re.search(r"cuda|cublas|cudnn|nvrtc|nvvm|nvjit|cufft|tvm|cutlass|nccl", p, re.I)
)
print(f"TOTAL_MAPPED {len(libs)}")
for p in interesting:
    print(f"LIB {p}")
