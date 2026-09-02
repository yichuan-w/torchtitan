#!/usr/bin/env python3
"""Is the SM103 backward hang deterministic, or does it sometimes pass?

This should have been the first experiment. Every round after the third was
built on the premise that adding prints turns a hang into a pass — a premise
that rests on three claims, one since disproved, one never independently
checked, and one that turned out to be a monitor matching the agent's own brief
text. Re-running the supposedly-passing build now hangs.

If the pristine kernel is not 100% deterministic, then some "HANG" and "PASS"
verdicts in that chain were noise, and conclusions drawn from single runs —
including which code change mattered — have to be discarded rather than
reinterpreted.

Runs the smallest failing configuration N times, each in its own process behind
a hard timeout, and reports the distribution.
"""
from __future__ import annotations

import argparse
import os
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path("/scratch/gpfs/TRIDAO/al9080")
PY = ROOT / "titan-rl/bin/python"
CHILD = ROOT / "fa4-fix/_optlevel_one.py"
PRISTINE = ROOT / "fa4-fix/orig/flash_bwd_sm100.py"
TARGET = ROOT / ("titan-rl/lib/python3.12/site-packages/flash_attn/cute/"
                 "flash_bwd_sm100.py")


def one(timeout: int) -> str:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "6"}
    try:
        p = subprocess.run([str(PY), str(CHILD)], capture_output=True,
                           text=True, timeout=timeout, env=env)
        if "BWD_OK" in p.stdout:
            return "PASS"
        if "FWD_OK" in p.stdout:
            return "FWD_ONLY"
        return "ERROR"
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-9", "-f", CHILD.name])
        return "HANG"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=150)
    args = ap.parse_args()

    # Guarantee we are measuring the unmodified kernel.
    if PRISTINE.read_bytes() != TARGET.read_bytes():
        import shutil
        shutil.copy2(PRISTINE, TARGET)
        print("installed tree differed from pristine; restored before measuring")

    results = []
    for i in range(1, args.runs + 1):
        r = one(args.timeout)
        results.append(r)
        print(f"run {i:2d}/{args.runs}: {r}", flush=True)
    c = Counter(results)
    print(f"\n{dict(c)}")
    if len(c) == 1:
        print(f"deterministic: {results[0]} in {args.runs}/{args.runs} runs")
    else:
        print("NOT deterministic — single-run verdicts in this investigation "
              "cannot be trusted")


if __name__ == "__main__":
    main()
