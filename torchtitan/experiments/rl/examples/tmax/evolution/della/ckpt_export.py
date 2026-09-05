#!/usr/bin/env python3
"""Export a run's checkpoints as bf16 weights, so the whole curve is keepable.

    TRL_BASE=... TRL_TT=... TRL_MODEL=... ckpt_export.py [--run <dir>] [--max N]

A training checkpoint is 109 GB, and 90 of that is optimizer state: fp32 master
weights plus Adam's two moments, twelve bytes a parameter. Only a resume ever
reads it, and only the last few steps are ever resumed from, which is why the
trainer keeps three and `ckpt_mirror.sh` copies one off the box. Everything
else a checkpoint is wanted for later -- evaluating a step, comparing two,
publishing one -- needs the weights alone: 18 GB in bf16 (measured on a real
step; the size of the base model), a sixth of a checkpoint.

So this converts each complete step to Hugging Face safetensors under the run's
own `weights/step-<N>/`, on the shared filesystem, and keeps every one of them:
33 steps of a run to step 100 cost 600 GB against 3.6 TB for the full states.
The conversion reads only the model tensors out of the DCP checkpoint (the
optimizer shards are never opened), runs on the CPU, needs about 60 GB of RAM
for the fp32 model plus its bf16 copy, and took 93 s for one 9B step.

Idempotent and interruptible: an export lands in `.incoming` and is renamed in,
a step already exported is skipped, and one lock keeps two timers from
converting the same step twice.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402

_STEP = re.compile(r"^step-(\d+)$")
_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth", ".ckpt")


def complete_steps(ckpt_dir: Path, settle_sec: int = 120) -> list[tuple[int, Path]]:
    """Steps whose save has finished: no ``*.tmp`` and untouched for two minutes.

    The same test ``eval_watcher.sh`` and ``ckpt_mirror.sh`` use. A save in
    flight has its shards open, and converting one produces a file that looks
    like a checkpoint and is half a step old.
    """
    out = []
    now = time.time()
    for child in sorted(ckpt_dir.iterdir() if ckpt_dir.is_dir() else []):
        m = _STEP.match(child.name)
        if not m or not child.is_dir():
            continue
        if any(child.glob("*.tmp")) or not (child / ".metadata").is_file():
            continue
        if now - child.stat().st_mtime < settle_sec:
            continue
        out.append((int(m.group(1)), child))
    return sorted(out)


def copy_assets(assets: Path, out: Path) -> None:
    """Everything the base model directory holds except its weights: config,
    tokenizer, chat template. Without them the export is a bag of tensors that
    no loader will open."""
    for source in assets.rglob("*"):
        if not source.is_file():
            continue
        rel = source.relative_to(assets)
        if any(part in {".cache", ".git"} for part in rel.parts):
            continue
        if source.name.endswith(_WEIGHT_SUFFIXES) or source.name == "model.safetensors.index.json":
            continue
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, out / rel)


def export_step(src: Path, dst: Path, assets: Path, dtype: str = "bfloat16") -> None:
    """One DCP checkpoint to one HF directory, whole or not at all."""
    from scripts.checkpoint_conversion.convert_to_hf import convert_to_hf

    incoming = dst.with_name(dst.name + ".incoming")
    shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir(parents=True)
    try:
        convert_to_hf(input_dir=src, output_dir=incoming, model_name="qwen3_5",
                      model_flavor="9B", hf_assets_path=assets, export_dtype=dtype)
        # The writer consolidates its shards into model-0000N-of-0000M.safetensors
        # and leaves the shards in sharded/, which is the same 19 GB again.
        shutil.rmtree(incoming / "sharded", ignore_errors=True)
        copy_assets(assets, incoming)
        os.replace(incoming, dst)
    except BaseException:
        shutil.rmtree(incoming, ignore_errors=True)
        raise


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, help="run directory (default: $TRL_BASE/runs/latest)")
    ap.add_argument("--max", type=int, default=1,
                    help="how many steps to export in one invocation (default 1: a timer "
                         "catches up over its next firings instead of holding the CPU)")
    ap.add_argument("--dtype", default="bfloat16", choices=("bfloat16", "float16", "float32"))
    a = ap.parse_args()

    root = layout.Root.from_env()
    run = layout.Run((a.run or (root.runs / "latest")).resolve())
    if not run.path.is_dir():
        raise SystemExit(f"no run directory at {a.run or root.runs / 'latest'}")
    assets = Path(os.environ.get("TRL_MODEL", ""))
    if not assets.is_dir():
        raise SystemExit("TRL_MODEL must name the base model directory (config, tokenizer)")
    ckpt = run.checkpoints.resolve()
    if not ckpt.is_dir():
        raise SystemExit(0)

    log = root.logs / "ckpt_export.log"
    root.logs.mkdir(parents=True, exist_ok=True)
    lock = open(root.logs / "ckpt_export.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another invocation is converting; its next firing catches up

    done = 0
    for step, src in complete_steps(ckpt):
        dst = run.weights / f"step-{step}"
        if dst.is_dir():
            continue
        started = time.time()
        try:
            export_step(src, dst, assets, a.dtype)
        except Exception as e:  # noqa: BLE001 -- one step, not the timer
            layout.append_jsonl(log.with_suffix(".jsonl"), {
                "stamp": layout.stamp(), "run": run.name, "step": step,
                "status": "failed", "error": f"{type(e).__name__}: {e}"[:300]})
            print(f"export step-{step} failed: {type(e).__name__}: {e}", file=sys.stderr)
            raise SystemExit(1)
        layout.append_jsonl(log.with_suffix(".jsonl"), {
            "stamp": layout.stamp(), "run": run.name, "step": step, "status": "ok",
            "secs": round(time.time() - started, 1),
            "bytes": sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())})
        print(f"exported {dst} in {time.time() - started:.0f}s")
        done += 1
        if done >= a.max:
            break


if __name__ == "__main__":
    main()
