#!/usr/bin/env python
"""Check that this directory still reproduces its own published results.

The pipeline has one stage that is not bit-reproducible — the agents' judgement
— so reproducibility here means three different things, and this script checks
each separately rather than pretending they are the same:

  A  corpus      work/cache/*.jsonl match cache_manifest.json          (exact)
  B  retrieval   rebuilding packets from the cache gives the same bytes (exact)
  C  rerank      rerank/*.json all validate against the pool           (integrity)
  D  merge       re-deriving results/*.csv from rerank/ gives the same bytes (exact)

A, B and D are exact: a mismatch is a bug or a changed input. C is not a
replay — re-running the agents will produce different lists, and that is
expected; what is checked is that the 89 stored judgements are internally
consistent and reference only ids that exist.

  verify.py            A, C, D   (fast, no GPU)
  verify.py --full     also B    (re-runs retrieval; needs the GPU, ~12 min)
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
OUT = HERE / "results"
RERANK = HERE / "rerank"
PACKETS = HERE / "work" / "packets"
OUT_FILES = [
    "tb21_agentic_top10.csv",
    "tb21_agentic_top10_ids.csv",
    "tb21_agentic_per_source_top10.csv",
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=HERE, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="also re-run retrieval (GPU)")
    args = ap.parse_args()
    fails: list[str] = []

    print("A. corpus cache")
    r = run([PY, "bootstrap_cache.py", "--verify"])
    print("   " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
    if r.returncode:
        fails.append("corpus cache does not match cache_manifest.json")

    print("C. rerank integrity")
    n = len([p for p in RERANK.glob("*.json") if not p.name.startswith("_")])
    print(f"   {n} judgement files")
    if n != 89:
        fails.append(f"expected 89 rerank files, found {n}")

    print("D. merge replay")
    with tempfile.TemporaryDirectory() as td:
        r = run([PY, "merge_agentic.py", "--strict", "--out", td])
        if r.returncode:
            bad = [l for l in r.stdout.splitlines() if "PROBLEM" in l]
            fails.append(f"merge --strict failed ({len(bad)} problems)")
            for line in bad[:5]:
                print("   " + line.strip())
        for name in OUT_FILES:
            a, b = OUT / name, Path(td) / name
            if not a.exists():
                fails.append(f"{name} missing from results/")
            elif not b.exists():
                fails.append(f"{name} not produced by replay")
            elif not filecmp.cmp(a, b, shallow=False):
                fails.append(f"{name} differs from a fresh merge of rerank/")
            else:
                print(f"   {name}  {sha256(a)[:16]}  identical")

    if args.full:
        print("B. retrieval replay (rebuilding candidates + packets)")
        with tempfile.TemporaryDirectory() as td:
            cand, pack = Path(td) / "candidates", Path(td) / "packets"
            r = run([PY, "build_candidates.py", "--out", str(cand),
                     "--batch", "16", "--per-retriever", "25"])
            if r.returncode:
                fails.append("build_candidates.py failed on replay")
                print("   " + r.stderr.strip()[-400:])
            else:
                run([PY, "make_packets.py", "--src", str(cand), "--dst", str(pack)])
                diff = [
                    p.name for p in sorted(PACKETS.glob("*.json"))
                    if not (pack / p.name).exists()
                    or not filecmp.cmp(p, pack / p.name, shallow=False)
                ]
                if diff:
                    fails.append(f"{len(diff)} packets differ on replay, e.g. {diff[:3]}")
                else:
                    print(f"   {len(list(PACKETS.glob('*.json')))} packets identical")
    else:
        print("B. retrieval replay  SKIPPED (--full to run; needs GPU)")

    print()
    if fails:
        for f in fails:
            print(f"FAIL {f}")
        return 1
    print("OK — corpus, rerank integrity and merge all reproduce"
          + (" (retrieval too)" if args.full else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
