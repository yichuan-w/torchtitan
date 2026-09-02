#!/usr/bin/env python3
"""Try the block-level barrier the CUDA docs prescribe before an mbarrier wait.

Eight rounds tried warp-level synchronization (`cute.arch.sync_warp()`,
predicated and not) around the compute warp's LSE wait, and all of them hang.
But the guidance in the CUDA programming guide is about `__syncthreads()` — a
*block*-level barrier — "prior to wait_barrier to resolve thread divergence in
cases where all threads must wait on the mbarrier". That variant has never been
tested here, and it fits the SASS evidence: `BSYNC` waits for every thread that
entered the convergence barrier, which a warp-level sync cannot satisfy on its
own.

Applies the change, runs the smallest hanging configuration behind a hard
timeout, and restores the file either way.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/scratch/gpfs/TRIDAO/al9080")
TARGET = ROOT / ("titan-rl/lib/python3.12/site-packages/flash_attn/cute/"
                 "flash_bwd_sm100.py")
PRISTINE = ROOT / "fa4-fix/orig/flash_bwd_sm100.py"
CHILD = ROOT / "fa4-fix/_optlevel_one.py"
PY = ROOT / "titan-rl/bin/python"
ANCHOR = "pipeline_LSE.consumer_wait(consumer_state_LSE)"
MARKER = "# Prefetch 1 stage of LSE"


def patch(before: str, after: str) -> None:
    lines = PRISTINE.read_text().splitlines(keepends=True)
    out, applied = [], 0
    for i, ln in enumerate(lines):
        if ANCHOR in ln and i and MARKER in lines[i - 1]:
            indent = ln[:len(ln) - len(ln.lstrip())]
            if before:
                out.append(f"{indent}{before}\n")
            out.append(ln)
            if after:
                out.append(f"{indent}{after}\n")
            applied += 1
            continue
        out.append(ln)
    if applied != 1:
        sys.exit(f"expected one patch site, found {applied}")
    text = "".join(out)
    ast.parse(text)
    TARGET.write_text(text)


def run() -> str:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "6"}
    try:
        p = subprocess.run([str(PY), str(CHILD)], capture_output=True,
                           text=True, timeout=240, env=env)
        if "BWD_OK" in p.stdout:
            return "PASS"
        if "FWD_OK" in p.stdout:
            return "FWD_ONLY"
        return f"ERROR {(p.stderr or '')[-200:]}"
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-9", "-f", CHILD.name])
        return "HANG"


VARIANTS = [
    ("cute.arch.barrier()", "cute.arch.barrier()", "block barrier both sides"),
    ("cute.arch.barrier()", "", "block barrier before only"),
    ("", "cute.arch.barrier()", "block barrier after only"),
    ("cute.arch.barrier()", "cute.arch.sync_warp()", "block before, warp after"),
]


def main() -> None:
    try:
        for before, after, label in VARIANTS:
            patch(before, after)
            print(f"{label:32s} {run()}", flush=True)
    finally:
        shutil.copy2(PRISTINE, TARGET)
        print("restored to pristine")


if __name__ == "__main__":
    main()
