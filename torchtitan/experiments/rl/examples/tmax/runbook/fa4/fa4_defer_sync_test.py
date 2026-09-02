#!/usr/bin/env python3
"""Is the SM103 hang a deferred barrier initialization on the LSE pipeline?

Two asymmetries separate the pipeline that wedges from the ones that do not.
`pipeline_LSE` and `pipeline_dPsum` are the only two built as `PipelineTmaAsync`
with `defer_sync=True`, and the only two whose `cta_layout_vmnk` argument is
commented out; `pipeline_Q` and `pipeline_dO` are `PipelineTmaUmma` and pass it.

`defer_sync` postpones the initialization sync for the barrier. If a consumer
can reach `consumer_wait` before that initialization is visible, it waits on a
phase that never flips — which is what the SASS shows, and which no amount of
consumer-side synchronization can repair, matching every failed attempt so far.
CUTLASS 4.3.1 fixed a same-shaped SM103 bug by adding a missing `elect_one` to a
prefetch barrier's initialization.

Each variant runs the smallest hanging configuration behind a hard timeout and
the file is restored either way.
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


def patch(which: list[str]) -> None:
    """Turn defer_sync off for the named pipelines."""
    src = PRISTINE.read_text()
    lines = src.splitlines(keepends=True)
    out, current, changed = [], None, 0
    for ln in lines:
        for name in ("LSE", "dPsum", "Q", "dO", "dS"):
            if f"pipeline_{name} = " in ln:
                current = name
        if current in which and "defer_sync=True" in ln:
            out.append(ln.replace("defer_sync=True", "defer_sync=False"))
            changed += 1
            continue
        out.append(ln)
    if changed == 0:
        sys.exit(f"no defer_sync=True found for {which}")
    text = "".join(out)
    ast.parse(text)
    TARGET.write_text(text)
    return changed


def run() -> str:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "6"}
    try:
        p = subprocess.run([str(PY), str(CHILD)], capture_output=True,
                           text=True, timeout=300, env=env)
        if "BWD_OK" in p.stdout:
            return "PASS"
        if "FWD_OK" in p.stdout:
            return "FWD_ONLY (bwd hung or failed)"
        return f"ERROR {(p.stderr or '')[-200:]}"
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-9", "-f", CHILD.name])
        return "HANG"


def main() -> None:
    try:
        for which in (["LSE"], ["LSE", "dPsum"]):
            n = patch(which)
            print(f"defer_sync=False on {'+'.join(which):12s} "
                  f"({n} sites)  {run()}", flush=True)
    finally:
        shutil.copy2(PRISTINE, TARGET)
        print("restored to pristine")


if __name__ == "__main__":
    main()
