#!/usr/bin/env python3
"""Does FA4 forward work in the working environment, with the local patch?

The patch to flash_fwd_sm100.py exists only to get past a compile error against
a DSL the release does not pin. Removing it is the right cleanup — it turns an
honest compile failure into a hang, which is how twenty rounds got spent — but
only if nothing depends on forward still compiling there. Backward already
hangs, so this asks about forward alone.

Prints one line: whether forward compiled, ran, and returned finite values.
"""
from __future__ import annotations

import torch
import torch.nn.attention as A
from torch.nn.attention import sdpa_kernel, SDPBackend

A.activate_flash_attention_impl("FA4")
assert A.current_flash_attention_impl() == "FA4", "FA4 impl not active"

torch.manual_seed(0)
q, k, v = (torch.randn(2, 4, 256, 64, device="cuda", dtype=torch.bfloat16)
           for _ in range(3))
with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
    o = torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=False)
torch.cuda.synchronize()
print(f"forward ok: shape={tuple(o.shape)} finite={torch.isfinite(o).all().item()}")
