#!/usr/bin/env python3
"""Acceptance test for FA4 backward on B300 (SM103). This file is the goal.

A fix is accepted when this script prints ALL PASS. Nothing else counts — not
"it no longer hangs", not "the numbers look close". Two properties are checked
per configuration:

  1. It terminates. Each config runs in its own process with a hard timeout,
     because the failure being fixed is a kernel that never returns, which no
     in-process guard can interrupt.

  2. Its gradients are as accurate as PyTorch's own bf16 attention. Exact
     equality is the wrong bar — bf16 rounding makes every implementation
     differ from the truth — so the comparison is the one flash-attention's own
     test suite uses: compute a float32 reference, measure how far the bf16
     baseline lands from it, and require FA4 to land no more than twice as far.
     A kernel that returns quickly with silently wrong gradients is worse than
     one that hangs: an RL run would train on them for days before the loss
     curve gave anything away.

Forward is checked by the same rule, so a backward fix that breaks forward
cannot pass.

Disallowed "fixes": routing SM103 to another backend, disabling FA4 backward,
falling back to the math or mem-efficient path, or skipping configs. The point
is a working SM103 backward kernel, and this script verifies the FLASH backend
produced the gradients it returns.

Usage: python fa4_bwd_acceptance.py [--timeout 180]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

CHILD = Path(__file__).with_name("_fa4_acceptance_one.py")

CHILD_SRC = r'''
import json, sys, torch
import torch.nn.attention as A
from torch.nn.attention import sdpa_kernel, SDPBackend

B, H, S, D, causal = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5] == "1"
A.activate_flash_attention_impl("FA4")
assert A.current_flash_attention_impl() == "FA4", "FA4 impl not active"
torch.manual_seed(0)

base = [torch.randn(B, H, S, D, device="cuda", dtype=torch.bfloat16) for _ in range(3)]

def grads(backend, dtype):
    q, k, v = (t.detach().to(dtype).requires_grad_(True) for t in base)
    with sdpa_kernel(backend):
        o = torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=causal)
        o.sum().backward()
    torch.cuda.synchronize()
    return o.detach().float(), q.grad.float(), k.grad.float(), v.grad.float()

# float32 math attention is the ground truth; bf16 mem-efficient is the yardstick.
ref = grads(SDPBackend.MATH, torch.float32)
bas = grads(SDPBackend.EFFICIENT_ATTENTION, torch.bfloat16)
fa4 = grads(SDPBackend.FLASH_ATTENTION, torch.bfloat16)

names = ["out", "dq", "dk", "dv"]
report = {}
ok = True
for n, r, b, f in zip(names, ref, bas, fa4):
    err_base = (b - r).abs().max().item()
    err_fa4 = (f - r).abs().max().item()
    # 2x the baseline's own error, with a floor so an exact-zero baseline
    # (possible for tiny tensors) cannot make the bar unreachable.
    bar = max(2.0 * err_base, 1e-3)
    report[n] = {"err_fa4": err_fa4, "err_baseline": err_base, "bar": bar,
                 "ok": err_fa4 <= bar}
    ok &= report[n]["ok"]
print("RESULT " + json.dumps({"ok": ok, "detail": report}))
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()
    CHILD.write_text(CHILD_SRC)

    configs = [
        (b, h, s, d, c)
        for d, s, c in itertools.product([64, 128], [256, 512, 2048], [0, 1])
        for b, h in [(2, 4)]
    ]
    rows, failed = [], 0
    for (b, h, s, d, c) in configs:
        tag = f"B{b} H{h} S{s:<4d} D{d:<3d} causal={c}"
        cmd = [args.python, str(CHILD), str(b), str(h), str(s), str(d), str(c)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=args.timeout, env=os.environ)
            line = next((l for l in p.stdout.splitlines()
                         if l.startswith("RESULT ")), None)
            if line is None:
                verdict, detail = "ERROR", (p.stderr or p.stdout)[-300:]
            else:
                data = json.loads(line[len("RESULT "):])
                verdict = "PASS" if data["ok"] else "NUMERIC_FAIL"
                worst = max(data["detail"].items(),
                            key=lambda kv: kv[1]["err_fa4"] / max(kv[1]["bar"], 1e-12))
                detail = (f"worst {worst[0]}: err={worst[1]['err_fa4']:.3e} "
                          f"bar={worst[1]['bar']:.3e}")
        except subprocess.TimeoutExpired:
            verdict, detail = "HANG", f"no return within {args.timeout}s"
            subprocess.run(["pkill", "-9", "-f", CHILD.name])
        rows.append((tag, verdict, detail))
        failed += verdict != "PASS"
        print(f"{tag}  {verdict:12s} {detail}", flush=True)

    print()
    if failed == 0:
        print(f"ALL PASS ({len(rows)}/{len(rows)} configurations)")
        sys.exit(0)
    print(f"FAILED: {failed} of {len(rows)} configurations")
    sys.exit(1)


if __name__ == "__main__":
    main()
