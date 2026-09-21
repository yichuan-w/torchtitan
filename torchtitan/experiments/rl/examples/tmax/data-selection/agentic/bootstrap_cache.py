#!/usr/bin/env python
"""Rebuild work/cache/ — the six jsonl corpus dumps every later stage reads.

Stage 1 onwards only ever touch these files, never the original datasets, so
this is the one script that depends on the Harbor trees and HF parquets being
present. It reuses the loaders in ../compute_tb21_tfidf.py so the corpus text
is composed identically to the TF-IDF run.

Writes: work/cache/{TB2.1 + 5 corpora}.jsonl, plus a manifest with each file's
row count and sha256 so a later run can prove it read the same corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TFIDF = HERE.parent / "compute_tb21_tfidf.py"
CACHE = HERE / "work" / "cache"
MANIFEST = HERE / "cache_manifest.json"


def load_tfidf_module():
    if not TFIDF.exists():
        sys.exit(f"missing {TFIDF} — it holds the corpus loaders")
    spec = importlib.util.spec_from_file_location("tfidf_loaders", TFIDF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild files that exist")
    ap.add_argument("--verify", action="store_true",
                    help="only check existing files against cache_manifest.json")
    args = ap.parse_args()

    if args.verify:
        if not MANIFEST.exists():
            sys.exit("no cache_manifest.json to verify against")
        want = json.loads(MANIFEST.read_text())["files"]
        bad = []
        for name, meta in want.items():
            p = CACHE / name
            if not p.exists():
                bad.append(f"{name}: missing")
            elif (got := sha256(p)) != meta["sha256"]:
                bad.append(f"{name}: sha256 {got[:12]} != {meta['sha256'][:12]}")
        for line in bad:
            print(f"[cache] MISMATCH {line}")
        print(f"[cache] {len(want) - len(bad)}/{len(want)} files match the manifest")
        return 1 if bad else 0

    m = load_tfidf_module()
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    jobs: list[tuple[str, str, callable]] = [
        ("tb21_docs.jsonl", "TB2.1", m.load_tb21),
    ]
    for name, root in m.LOCAL_TREES.items():
        jobs.append(
            (f"{name}.jsonl", name,
             lambda n=name, r=root: m.load_tree_corpus(n, r, include_oracle=True))
        )
    for corpus in m.HF_CORPORA:
        jobs.append(
            (f"{corpus['name']}.jsonl", corpus["name"],
             lambda c=corpus: m.load_hf_parquet(c))
        )

    files = {}
    for fname, label, loader in jobs:
        path = CACHE / fname
        if path.exists() and not args.force:
            n = sum(1 for line in path.open() if line.strip())
            print(f"[cache] {label}: kept {n} rows (--force to rebuild)")
        else:
            rows = loader()
            m.dump_jsonl(path, rows)
            n = len(rows)
            print(f"[cache] {label}: wrote {n} rows")
        files[fname] = {"rows": n, "sha256": sha256(path), "bytes": path.stat().st_size}

    MANIFEST.write_text(json.dumps(
        {
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_sec": round(time.time() - t0, 1),
            "source_paths": {
                "tb21_trees": str(m.TB21_TREES),
                **{k: str(v) for k, v in m.LOCAL_TREES.items()},
                **{c["name"]: c["hf"] for c in m.HF_CORPORA},
            },
            "files": files,
        },
        indent=2,
    ))
    total = sum(f["rows"] for k, f in files.items() if k != "tb21_docs.jsonl")
    print(f"[cache] pool={total} rows, queries={files['tb21_docs.jsonl']['rows']}")
    print(f"[cache] manifest -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
