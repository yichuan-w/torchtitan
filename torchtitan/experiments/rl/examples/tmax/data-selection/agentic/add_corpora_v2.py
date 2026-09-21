#!/usr/bin/env python
"""Add Terminal-Lego-15k and CalibForge to work/cache/ for the v2 pool.

Both ship as Harbor task trees, the same shape the five v1 corpora were read
from, so they go through compute_tb21_tfidf.py's own composer -- the pooled text
is then built identically across all seven corpora and the retrievers see one
consistent representation.

CalibForge's two subsets (contrastive_solver, multi_solver) are pooled under one
corpus name; which subset a task came from is kept in `origin` and in
`subset`, because the two were calibrated differently and a later step may want
to weight them apart.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "work" / "cache"
TFIDF = HERE.parent / "compute_tb21_tfidf.py"

LEGO = Path("/data/yichuan_wang/terminal-lego-15k")
CALIBFORGE = Path("/data/yichuan_wang/calibforge")


def loaders():
    spec = importlib.util.spec_from_file_location("tfidf_loaders", TFIDF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compose_dir(m, task_dir: Path, source: str, subset: str | None = None) -> dict | None:
    text = m.compose_from_tree(task_dir, include_oracle=True)
    if not text:
        return None
    inst = task_dir / "instruction.md"
    row = {
        "task_id": task_dir.name,
        "instruction": m.read_file(inst, m.CAP["instruction"]) if inst.exists() else "",
        "text": text,
        "source": source,
        "origin": str(task_dir),
        "chars": len(text),
    }
    if subset:
        row["subset"] = subset
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    m = loaders()
    CACHE.mkdir(parents=True, exist_ok=True)

    jobs = []
    if LEGO.exists():
        jobs.append(("Terminal-Lego-15k",
                     [(d, None) for d in sorted(LEGO.glob("task_*")) if (d / "instruction.md").exists()]))
    else:
        print(f"[v2] MISSING {LEGO}", file=sys.stderr)
    if CALIBFORGE.exists():
        dirs = []
        for sub in ("contrastive_solver", "multi_solver"):
            base = CALIBFORGE / sub
            if base.exists():
                dirs += [(d, sub) for d in sorted(base.iterdir())
                         if d.is_dir() and (d / "instruction.md").exists()]
        jobs.append(("CalibForge", dirs))
    else:
        print(f"[v2] MISSING {CALIBFORGE}", file=sys.stderr)

    for name, dirs in jobs:
        out = CACHE / f"{name}.jsonl"
        if out.exists() and not args.force:
            n = sum(1 for line in out.open() if line.strip())
            print(f"[v2] {name}: kept {n} rows (--force to rebuild)")
            continue
        rows = []
        for i, (d, sub) in enumerate(dirs, 1):
            r = compose_dir(m, d, name, sub)
            if r:
                rows.append(r)
            if i % 2000 == 0:
                print(f"[v2] {name}: {i}/{len(dirs)}")
        with out.open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        mean = sum(r["chars"] for r in rows) / max(len(rows), 1)
        print(f"[v2] {name}: wrote {len(rows)} rows, mean_chars={mean:.0f} -> {out}")

    tot = 0
    for p in sorted(CACHE.glob("*.jsonl")):
        if p.stem == "tb21_docs":
            continue
        n = sum(1 for line in p.open() if line.strip())
        tot += n
        print(f"    {p.stem:28} {n:6}")
    print(f"[v2] pool now {tot} training tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
