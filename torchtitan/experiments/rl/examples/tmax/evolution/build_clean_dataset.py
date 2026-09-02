#!/usr/bin/env python3
"""Assemble TerminalWorld-Seeds-Clean: the oracle-validated subset.

Merges one or more oracle_validation result files (tridao + laptop runs),
keeps tasks whose reference solution passed the verifier end-to-end, and
repackages them in the same RST-style layout as the parent dataset.

Usage:
  uv run python scripts/build_clean_dataset.py \
      --results results/oracle_validation.jsonl [results/other.jsonl ...] \
      [--out data/seed-dataset-clean] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "data" / "seed-dataset"


def merge_results(paths: list[Path]) -> tuple[dict[str, str], set[str]]:
    """Return (task_id -> latest verdict, tasks that flipped between verdicts).

    Taking the *best* verdict across attempts inflates the pass count: a task
    that passed once and failed later is not one whose reference solution
    reliably satisfies its verifier. Attempts are ordered by start time and the
    last verdict wins; tasks seen both passing and failing are reported so a
    consumer can exclude them.
    """
    attempts: dict[str, list] = {}
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            attempts.setdefault(r["task_id"], []).append(r)

    latest: dict[str, str] = {}
    flaky: set[str] = set()
    for tid, rs in attempts.items():
        rs.sort(key=lambda r: r.get("t_start", 0))
        verdicts = [r["status"] for r in rs
                    if r["status"] in ("pass", "fail", "skipped")]
        if not verdicts:
            continue
        latest[tid] = verdicts[-1]
        if "pass" in verdicts and "fail" in verdicts:
            flaky.add(tid)
    return latest, flaky


def main() -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    ap = argparse.ArgumentParser()
    ap.add_argument("--results", nargs="+", required=True)
    ap.add_argument("--out", default="data/seed-dataset-clean")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    status, flaky = merge_results([ROOT / p for p in args.results])
    passing = {t for t, s in status.items() if s == "pass"}
    print(f"results merged: {len(status)} judged, {len(passing)} pass "
          f"({len(flaky & passing)} of them flipped verdicts at least once)")
    if args.dry_run:
        return

    out = ROOT / args.out
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "metadata").mkdir(parents=True, exist_ok=True)

    # filter parquet rows
    t = pq.read_table(SRC / "metadata" / "tasks.parquet")
    mask = [tid in passing for tid in t["task_id"].to_pylist()]
    t_clean = t.filter(pa.array(mask))

    # filter tar members
    kept = 0
    with tarfile.open(SRC / "data" / "tasks-00000.tar") as src_tar, \
         tarfile.open(out / "data" / "tasks-00000.tar", "w") as dst_tar:
        for m in src_tar.getmembers():
            tid = m.name.split("/")[1] if "/" in m.name else ""
            if tid not in passing:
                continue
            if m.isfile():
                data = src_tar.extractfile(m).read()
                info = tarfile.TarInfo(m.name)
                info.size = len(data)
                info.mtime = 0
                dst_tar.addfile(info, io.BytesIO(data))
            kept += 1

    # mark the flipped ones so a consumer can drop them without re-deriving
    import pyarrow as pa2
    t_clean = t_clean.append_column(
        "verdict_flipped",
        pa2.array([tid in flaky for tid in t_clean["task_id"].to_pylist()]))
    pq.write_table(t_clean, out / "metadata" / "tasks.parquet")
    shard = out / "data" / "tasks-00000.tar"
    digest = hashlib.sha256(shard.read_bytes()).hexdigest()
    (out / "metadata" / "shard_manifest.jsonl").write_text(json.dumps({
        "shard": "data/tasks-00000.tar", "task_count": t_clean.num_rows,
        "size_bytes": shard.stat().st_size, "sha256": digest}) + "\n")
    (out / "metadata" / "validation_summary.json").write_text(json.dumps({
        "judged": len(status), "pass": len(passing),
        "pass_with_flipped_verdict": len(flaky & passing),
        "verdict_rule": "latest attempt by start time; not best-of",
        "by_status": {s: sum(1 for v in status.values() if v == s)
                      for s in set(status.values())}}, indent=2))
    print(f"clean dataset: {t_clean.num_rows} tasks -> {out}")


if __name__ == "__main__":
    main()
