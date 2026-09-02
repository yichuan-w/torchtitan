"""Time the 9B lm_head chunk matmul, forward + backward, under each precision path.

The trainer's ChunkedLossWrapper feeds CastLinear one chunk at a time:
[65536 / SWE_LOSS_CHUNKS, 4096] hidden states against the [248320, 4096] weight.
This runs that exact shape on one GPU under four paths and reports wall time and
error against the IEEE fp32 result, so a runbook claim about the lm_head cost can
be re-measured rather than inferred from a trace.

    CUDA_VISIBLE_DEVICES=7 python bench_lmhead_tf32.py [--tokens 8192] [--iters 5]

Measured 2026-09-02 on della-tridao (B300, torch 2.14.0.dev20260806+cu130), fwd+bwd:
    ieee_fp32 1302 ms | plain_tf32 127 ms | old_tf32x3_fwd_only 945 ms | new_tf32x3_fwd_bwd 223 ms
    rel err vs ieee: new path out 7.3e-6, grad_x 1.3e-5, grad_w 9.6e-6; plain TF32 grad_x 6.2e-4, grad_w 2.1e-4
"""

from __future__ import annotations

import argparse
import os
import time

import torch
import torch.nn.functional as F

from torchtitan.experiments.rl.models.cast_linear import _LinearTF32, _split_tf32


def _timed(fn, iters):
    fn()  # warm-up
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / iters * 1e3


def _rel_err(got, ref):
    return ((got - ref).norm() / ref.norm()).item()


def _old_tf32x3_forward(x, w):
    """The pre-2026-09-02 path: 3xTF32 in forward, autograd's plain fp32 backward."""
    prev = torch.get_float32_matmul_precision()
    torch.set_float32_matmul_precision("high")
    try:
        xh, xl = _split_tf32(x)
        wh, wl = _split_tf32(w)
        return F.linear(xh, wh) + F.linear(xh, wl) + F.linear(xl, wh)
    finally:
        torch.set_float32_matmul_precision(prev)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=int, default=65536 // 8)
    ap.add_argument("--hidden", type=int, default=4096)
    ap.add_argument("--vocab", type=int, default=248320)
    ap.add_argument("--iters", type=int, default=5)
    args = ap.parse_args()
    dev = "cuda"
    torch.manual_seed(0)
    # bf16-exact operands, as in the trainer (decoder norm output, FSDP bf16 weight)
    x_bf = torch.randn(args.tokens, args.hidden, device=dev, dtype=torch.bfloat16)
    w_bf = (torch.randn(args.vocab, args.hidden, device=dev, dtype=torch.bfloat16) * 0.02)
    go = torch.randn(args.tokens, args.vocab, device=dev, dtype=torch.float32)

    def run(path):
        x = x_bf.float().requires_grad_(True)
        w = w_bf.float().requires_grad_(True)
        prev = torch.get_float32_matmul_precision()
        try:
            if path == "ieee_fp32":
                torch.set_float32_matmul_precision("highest")
                out = F.linear(x, w)
            elif path == "plain_tf32":
                torch.set_float32_matmul_precision("high")
                out = F.linear(x, w)
            elif path == "old_tf32x3_fwd_only":
                torch.set_float32_matmul_precision("highest")
                out = _old_tf32x3_forward(x, w)
            elif path == "new_tf32x3_fwd_bwd":
                torch.set_float32_matmul_precision("highest")
                out = _LinearTF32.apply(x, w, True, True)
            else:
                raise ValueError(path)
            out.backward(go)
        finally:
            torch.set_float32_matmul_precision(prev)
        return out.detach(), x.grad, w.grad

    print(f"shape: [{args.tokens},{args.hidden}] x [{args.hidden},{args.vocab}]  "
          f"device={torch.cuda.get_device_name()}  torch={torch.__version__}")
    ref = run("ieee_fp32")
    for path in ("ieee_fp32", "plain_tf32", "old_tf32x3_fwd_only", "new_tf32x3_fwd_bwd"):
        out, gx, gw = run(path)
        ms = _timed(lambda: run(path), args.iters)
        print(f"{path:22s} fwd+bwd {ms:8.1f} ms   rel err: out {_rel_err(out, ref[0]):.1e}  "
              f"grad_x {_rel_err(gx, ref[1]):.1e}  grad_w {_rel_err(gw, ref[2]):.1e}")


if __name__ == "__main__":
    main()
