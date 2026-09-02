#!/usr/bin/env python3
"""Move a model-only DCP checkpoint from the trainer to the eval host.

della-tridao can open TCP:22 to flow-matic but holds no key for it, and agent
forwarding is disabled on the della hop; flow-matic cannot reach della at all
(Princeton VPN). Both, however, reach the Hugging Face Hub, so a private repo is
the transport -- and it doubles as a durable record of which weights produced
which eval number, which a direct copy would not leave behind.

    # on della
    python3 ship_checkpoint.py push  <local-dcp-dir> --step 80
    # on flow-matic
    python3 ship_checkpoint.py pull  <local-dcp-dir> --step 80

The repo is private. What lands in it is model weights and a DCP metadata blob;
no paths, no credentials, nothing about the task corpus.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")

from huggingface_hub import HfApi, snapshot_download  # noqa: E402

REPO = os.environ.get("TW_EVAL_CKPT_REPO", "andylizf/tw-eval-ckpt")
log = logging.getLogger("ship")


def push(src: Path, step: int) -> None:
    api = HfApi()
    api.create_repo(REPO, private=True, exist_ok=True, repo_type="model")
    nbytes = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
    log.info("uploading %s (%.1f GiB) -> %s:%s",
             src, nbytes / 2**30, REPO, f"step-{step}")
    t0 = time.time()
    api.upload_folder(repo_id=REPO, folder_path=str(src),
                      path_in_repo=f"step-{step}", repo_type="model")
    dt = time.time() - t0
    log.info("uploaded in %.0fs (%.1f MB/s)", dt, nbytes / dt / 1e6)


def pull(dst: Path, step: int) -> None:
    t0 = time.time()
    p = snapshot_download(REPO, repo_type="model",
                          allow_patterns=[f"step-{step}/*"],
                          local_dir=str(dst.parent), max_workers=16)
    got = Path(p) / f"step-{step}"
    nbytes = sum(f.stat().st_size for f in got.rglob("*") if f.is_file())
    dt = time.time() - t0
    log.info("downloaded %.1f GiB to %s in %.0fs (%.1f MB/s)",
             nbytes / 2**30, got, dt, nbytes / dt / 1e6)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["push", "pull"])
    ap.add_argument("path", type=Path)
    ap.add_argument("--step", type=int, required=True)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    (push if args.action == "push" else pull)(args.path, args.step)


if __name__ == "__main__":
    main()
