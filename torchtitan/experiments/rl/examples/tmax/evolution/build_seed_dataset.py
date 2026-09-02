#!/usr/bin/env python3
"""Build the seed-level HF dataset: TerminalWorld tasks repackaged in the exact
release layout of RST (Zhongzhi1228/Recursive-Task-Synthesis).

RST released only the synthesized rounds; its 639 seeds were sampled from
TerminalWorld (EuniAI/TerminalWorld). This packages ALL 1,530 TW tasks (the
seed superset) in RST's format so our own synthesis can start from round 0:

    data/tasks-00000.tar            five-piece task dirs (<=5000 per shard)
    metadata/tasks.parquet          RST's column schema + TW extras appended
    metadata/shard_manifest.jsonl   shard sha256 manifest

Task content is kept byte-verbatim (including TerminalWorld's harbor-canary
markers — stripping them would defeat their purpose).

Usage: uv run python scripts/build_seed_dataset.py
Output: data/seed-dataset/   Log: logs/build_seed_dataset.log
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TW = ROOT / "data" / "terminalworld-data"
OUT = ROOT / "data" / "seed-dataset"
LOGS = ROOT / "logs"
SHARD_SIZE = 5000  # tasks per tar shard, matching RST

REQUIRED = ["instruction.md", "task.toml", "environment/Dockerfile", "solution/solve.sh"]

log = logging.getLogger("build")


def load_tw_metadata() -> tuple[dict, set]:
    meta = {}
    with gzip.open(TW / "data" / "full.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            meta[r["task_id"]] = r
    verified = set((TW / "splits" / "verified.txt").read_text().split())
    return meta, verified


def content_sha256(files: list[tuple[str, bytes]]) -> str:
    """sha256 over sorted (relpath, bytes) pairs — deterministic task fingerprint."""
    h = hashlib.sha256()
    for rel, data in sorted(files):
        h.update(rel.encode())
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(LOGS / "build_seed_dataset.log")])

    import pyarrow as pa
    import pyarrow.parquet as pq

    meta, verified = load_tw_metadata()
    archives = sorted((TW / "artifacts").glob("tw_*.tar.gz"))
    log.info("input: %d archives, %d metadata rows, %d verified",
             len(archives), len(meta), len(verified))

    (OUT / "data").mkdir(parents=True, exist_ok=True)
    (OUT / "metadata").mkdir(parents=True, exist_ok=True)

    rows, skipped = [], []
    shard_idx, shard_count = 0, 0
    shard_path = OUT / "data" / f"tasks-{shard_idx:05d}.tar"
    shard_tar = tarfile.open(shard_path, "w")

    t0 = time.time()
    for i, arc in enumerate(archives):
        tw_id = arc.name.removesuffix(".tar.gz")
        try:
            with tarfile.open(arc, "r:gz") as tf:
                files = []
                for m in tf.getmembers():
                    if not m.isfile():
                        continue
                    rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
                    files.append((rel, tf.extractfile(m).read()))
        except Exception as e:  # noqa: BLE001 — record and move on
            skipped.append((tw_id, f"unreadable: {e}"))
            log.warning("SKIP %s: unreadable archive: %s", tw_id, e)
            continue

        have = {rel for rel, _ in files}
        missing = [r for r in REQUIRED if r not in have]
        if missing:
            skipped.append((tw_id, f"missing: {missing}"))
            log.warning("SKIP %s: missing %s", tw_id, missing)
            continue

        fmap = dict(files)
        member_prefix = f"tasks/{tw_id}"
        for rel, data in files:
            info = tarfile.TarInfo(f"{member_prefix}/{rel}")
            info.size = len(data)
            info.mtime = 0  # deterministic output
            shard_tar.addfile(info, io.BytesIO(data))

        m = meta.get(tw_id, {})
        rows.append({
            # RST schema columns
            "task_id": tw_id,
            "task_group_id": tw_id,  # seeds have no lineage; group == self
            "task_content_sha256": content_sha256(files),
            "validation_status": "tw_verified" if tw_id in verified else "tw_full",
            "instruction": fmap["instruction.md"].decode("utf-8", "replace"),
            "task_toml": fmap["task.toml"].decode("utf-8", "replace"),
            "solution": fmap["solution/solve.sh"].decode("utf-8", "replace"),
            "dockerfile": fmap["environment/Dockerfile"].decode("utf-8", "replace"),
            "member_prefix": member_prefix,
            "file_count": len(files),
            "archive_entry_count": len(files),
            "uncompressed_bytes": sum(len(d) for _, d in files),
            "shard": f"data/{shard_path.name}",
            # TW extras (appended; RST columns above are unchanged)
            "terminal_domain": m.get("terminal_domain", ""),
            "tw_source_type": m.get("source_type", ""),
        })
        shard_count += 1
        log.info("[%d/%d] packed %s (%d files)", i + 1, len(archives), tw_id, len(files))

        if shard_count >= SHARD_SIZE:
            shard_tar.close()
            shard_idx += 1
            shard_count = 0
            shard_path = OUT / "data" / f"tasks-{shard_idx:05d}.tar"
            shard_tar = tarfile.open(shard_path, "w")
    shard_tar.close()

    pq.write_table(pa.Table.from_pylist(rows), OUT / "metadata" / "tasks.parquet")

    with open(OUT / "metadata" / "shard_manifest.jsonl", "w") as f:
        for p in sorted((OUT / "data").glob("tasks-*.tar")):
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            n = sum(1 for r in rows if r["shard"] == f"data/{p.name}")
            f.write(json.dumps({"shard": f"data/{p.name}", "task_count": n,
                                "size_bytes": p.stat().st_size, "sha256": digest}) + "\n")

    with open(OUT / "metadata" / "skipped.jsonl", "w") as f:
        for tw_id, reason in skipped:
            f.write(json.dumps({"task_id": tw_id, "reason": reason}) + "\n")

    log.info("DONE in %.1fs: %d tasks packed, %d skipped -> %s",
             time.time() - t0, len(rows), len(skipped), OUT)


if __name__ == "__main__":
    main()
