#!/usr/bin/env python3
"""Minimal reproduction: FA4 backward does not return on cutlass-dsl 4.6.0/4.6.1.

Run each version in its own process with an external timeout, because the
failure is a kernel that never returns and cannot be interrupted from inside:

    pip install "flash-attn-4==4.0.0b26" --no-deps
    pip install "nvidia-cutlass-dsl==4.6.0" "quack-kernels>=0.5.3" \
                "apache-tvm-ffi>=0.1.12,<0.2"
    timeout 180 python fa4_repro_dsl_hang.py     # no return
    pip install "nvidia-cutlass-dsl==4.6.2"
    timeout 180 python fa4_repro_dsl_hang.py     # completes

Expected on a B300 (compute capability 10.3):

    4.6.0.dev0  BACKWARD_DONE
    4.6.0       stops after BACKWARD_BEGIN
    4.6.1       stops after BACKWARD_BEGIN
    4.6.2       BACKWARD_DONE

The version line matters more than it looks. `nvidia-cutlass-dsl` is a
meta-package whose runtime ships in `-libs-cu12` and `-libs-cu13`, and a process
loads one of them; an environment can report 4.6.2 while loading a cu13 runtime
still at 4.6.0, which is why this prints the mapped library rather than trusting
the version.
"""
from __future__ import annotations

import pathlib
import re

import torch
import torch.nn.attention as A
from torch.nn.attention import sdpa_kernel, SDPBackend


def report_environment() -> None:
    from importlib.metadata import version

    for pkg in ("flash-attn-4", "nvidia-cutlass-dsl",
                "nvidia-cutlass-dsl-libs-cu12", "nvidia-cutlass-dsl-libs-cu13",
                "quack-kernels", "apache-tvm-ffi", "torch"):
        try:
            print(f"  {pkg} {version(pkg)}")
        except Exception:
            print(f"  {pkg} (absent)")
    cap = torch.cuda.get_device_capability(0)
    print(f"  gpu {torch.cuda.get_device_name(0)} sm_{cap[0]}{cap[1]}")

    # Which runtime is actually mapped, since the version above may not say.
    torch.zeros(1, device="cuda")
    import cutlass  # noqa: F401
    for line in pathlib.Path("/proc/self/maps").read_text().splitlines():
        if re.search(r"cute_dsl_runtime|_cutlass_ir", line):
            print(f"  loaded {line.split()[-1]}")
            break


def main() -> None:
    print("ENVIRONMENT")
    report_environment()

    A.activate_flash_attention_impl("FA4")
    assert A.current_flash_attention_impl() == "FA4", "FA4 impl not active"

    torch.manual_seed(0)
    q, k, v = (
        torch.randn(2, 4, 256, 64, device="cuda", dtype=torch.bfloat16,
                    requires_grad=True)
        for _ in range(3)
    )

    with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
        out = torch.nn.functional.scaled_dot_product_attention(
            q, k, v, is_causal=False)
    torch.cuda.synchronize()
    print("FORWARD_DONE", flush=True)

    print("BACKWARD_BEGIN", flush=True)
    out.sum().backward()
    torch.cuda.synchronize()
    print("BACKWARD_DONE", flush=True)

    finite = all(t.grad is not None and torch.isfinite(t.grad).all()
                 for t in (q, k, v))
    print(f"GRADS_FINITE {finite}")


if __name__ == "__main__":
    main()
