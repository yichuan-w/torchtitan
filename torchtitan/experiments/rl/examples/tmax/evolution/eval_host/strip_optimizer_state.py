#!/usr/bin/env python3
"""Copy a training DCP checkpoint keeping only the model weights.

A step checkpoint is ~102 GiB, and two thirds of that is AdamW state: fp32
exp_avg and exp_avg_sq for every parameter. The TB-2.0 eval recipe loads with
``initial_load_model_only=True``, so it never reads any of it -- shipping it to
the eval host would be 66 GiB moved to be discarded on arrival.

Stripping rather than converting is deliberate. The output is still a native
torchtitan DCP, so the eval host loads it through the same
``initial_load_in_hf=False`` path the trainer's own validation uses, and no
format conversion sits between the number this produces and the number the
training curve shows. That comparability is the only reason the eval host exists.

    python3 strip_optimizer_state.py <src-step-dir> <dst-dir> [--bf16]

``--bf16`` halves the result again (35 GiB -> 18) by casting the floating point
tensors. The eval's generator runs bf16 regardless, so the forward pass sees the
same values either way; it is off by default because the transfer budget does
not need it and "exactly the trainer's bytes" is a cheaper thing to defend.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import torch
import torch.distributed.checkpoint as dcp
from torch.distributed.checkpoint import state_dict_loader as sdl

log = logging.getLogger("strip")

# Everything the trainer writes that is not the model. `train_state` carries the
# step counter and policy version; `optimizer` is the AdamW moments.
DROP_PREFIXES = ("optimizer.", "train_state.")


def model_keys(src: Path) -> list[str]:
    md = dcp.FileSystemReader(src).read_metadata()
    return [k for k in md.state_dict_metadata
            if not k.startswith(DROP_PREFIXES)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--bf16", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    keys = model_keys(args.src)
    log.info("%d model keys (of %d total)", len(keys),
             len(dcp.FileSystemReader(args.src).read_metadata().state_dict_metadata))

    # `_load_state_dict_from_keys` rather than dcp.load with an empty-state-dict
    # planner: that planner builds a NESTED dict through `set_element` and does
    # not populate the caller's object, so the load silently yields nothing.
    # This returns the flat {metadata_key: tensor} mapping, which is also the
    # shape dcp.save needs to write metadata keys identical to the source's.
    t0 = time.time()
    sd = sdl._load_state_dict_from_keys(
        keys=set(keys), storage_reader=dcp.FileSystemReader(args.src))
    log.info("read %d tensors in %.0fs", len(sd), time.time() - t0)
    if not sd:
        raise SystemExit("read nothing -- the key filter matched no tensors")

    if args.bf16:
        sd = {k: (v.to(torch.bfloat16)
                  if torch.is_tensor(v) and v.is_floating_point() else v)
              for k, v in sd.items()}
        log.info("cast floating point tensors to bfloat16")

    args.dst.mkdir(parents=True, exist_ok=True)
    t1 = time.time()
    dcp.save(sd, storage_writer=dcp.FileSystemWriter(args.dst))
    log.info("wrote %s in %.0fs", args.dst, time.time() - t1)

    nbytes = sum(f.stat().st_size for f in args.dst.rglob("*") if f.is_file())
    log.info("done: %.1f GiB, %d keys, %.0fs total",
             nbytes / 2**30, len(sd), time.time() - t0)


if __name__ == "__main__":
    main()
