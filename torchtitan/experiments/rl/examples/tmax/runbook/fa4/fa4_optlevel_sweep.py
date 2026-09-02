#!/usr/bin/env python3
"""Does the SM103 backward hang depend on the ptxas optimization level?

The SASS diff showed the hanging build carries `BSSY B0 … BSYNC B0` around the
LSE wait while the passing (print-instrumented) build does not. That barrier
placement is chosen by ptxas, not by anything the DSL emits — MLIR is identical
between the two. So the optimization level is the cheapest lever that could
change it, and if some level does not hang, we get both a usable workaround and
direct evidence that this belongs to ptxas rather than to the kernel source.

Each level runs the smallest hanging configuration in its own process behind a
hard timeout, because the failure being probed never returns.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PY = "/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python"
CHILD = Path("/scratch/gpfs/TRIDAO/al9080/fa4-fix/_optlevel_one.py")

CHILD_SRC = r'''
import torch, torch.nn.attention as A
from torch.nn.attention import sdpa_kernel, SDPBackend
A.activate_flash_attention_impl("FA4")
q, k, v = (torch.randn(2, 4, 256, 64, device="cuda", dtype=torch.bfloat16,
                       requires_grad=True) for _ in range(3))
with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
    o = torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=False)
    torch.cuda.synchronize()
    print("FWD_OK", flush=True)
    o.sum().backward()
    torch.cuda.synchronize()
print("BWD_OK", flush=True)
'''


def main() -> None:
    CHILD.write_text(CHILD_SRC)
    rows = []
    for opt in ["", "opt-level=0", "opt-level=1", "opt-level=2", "opt-level=3"]:
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": "6"}
        if opt:
            env["CUTE_DSL_COMPILER_OPT"] = opt
        label = opt or "(default)"
        try:
            p = subprocess.run([PY, str(CHILD)], capture_output=True, text=True,
                               timeout=240, env=env)
            out = p.stdout
            verdict = ("BWD_OK" if "BWD_OK" in out else
                       "FWD_ONLY" if "FWD_OK" in out else "FAILED")
            if verdict == "FAILED":
                verdict += f" rc={p.returncode} {(p.stderr or '')[-160:]}"
        except subprocess.TimeoutExpired:
            verdict = "HANG"
            subprocess.run(["pkill", "-9", "-f", CHILD.name])
        rows.append((label, verdict))
        print(f"{label:16s} {verdict}", flush=True)
    ok = [r for r in rows if r[1] == "BWD_OK"]
    print("\npassing levels:", ok or "none")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
