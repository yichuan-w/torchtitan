#!/usr/bin/env python3
"""Materialize tmax mix tasks as task packages so the evolve loop can retune them.

The evolution loop resolves a signal's task id to a package directory
(instruction.md + environment/Dockerfile + tests/test.sh) under
data/sources/<corpus>/tasks;
tmax rows exist only as prepared jsonl rows, so their signals have always
died as no_pool_dir. This writes one package per train-split tmax task,
built purely from the row's own fields:

  instruction.md          <- metadata.problem_statement
  environment/Dockerfile  <- FROM <metadata.image> + WORKDIR (round-trips
                             through prepare_rts_data's workdir extraction)
  tests/test.sh           <- metadata.tmax.test_sh (writes /logs/verifier/
                             reward.txt, the contract _to_row checks)
  task.toml               <- [environment] cpus/memory_mb from the dataset's
                             measured peaks (the sizing channel _to_row reads)
  solution/solve.sh       <- marked stub: this corpus ships no reference
                             solution script. The k/k->harder operator writes
                             a fresh solve.sh for the evolved task anyway, and
                             Daytona revalidation requires that NEW script to
                             reach reward 1.0 before the task can fold in.

Fixtures are not handled: all 14,601 prepared rows carry fixtures == {}.
Run on della:

    python3.11 scripts/materialize_tmax_pool.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout

STUB_SOLVE = """#!/bin/bash
# No reference solution ships with this corpus (Tmax-Tasks-Clean carries
# verifier golds, not solve scripts). If you are evolving this task: write a
# complete solve.sh for the evolved task from the instruction, environment
# and verifier -- revalidation runs it on a fresh sandbox and requires
# reward 1.0.
echo "no reference solution" >&2
exit 1
"""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-parquet", default=None,
                    help="default: $TRL_BASE/data/sources/tmax-clean/splits/train.parquet")
    ap.add_argument("--prepared", default=None,
                    help="prepare_rts_data output; default: $TRL_BASE/data/tmax_train.jsonl")
    ap.add_argument("--out-root", default=None,
                    help="default: $TRL_BASE/data/sources/tmax-extract/tasks")
    ap.add_argument("--force", action="store_true",
                    help="rewrite packages that already exist")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = layout.Root.from_env()
    train_parquet = (Path(args.train_parquet) if args.train_parquet
                     else root.data / "sources" / "tmax-clean" / "splits" / "train.parquet")
    prepared = Path(args.prepared) if args.prepared else root.data / "tmax_train.jsonl"
    out_root = (Path(args.out_root) if args.out_root
                else root.data / "sources" / "tmax-extract" / "tasks")

    import pyarrow.parquet as pq
    t = pq.read_table(train_parquet, columns=[
        "task_id", "peak_ram_mb", "peak_disk_mb"])
    want: dict[str, dict] = {}
    for i in range(t.num_rows):
        want[t.column("task_id")[i].as_py()] = {
            "peak_ram_mb": t.column("peak_ram_mb")[i].as_py(),
            "peak_disk_mb": t.column("peak_disk_mb")[i].as_py(),
        }

    written, skipped, missing = [], [], []
    for line in open(prepared):
        row = json.loads(line)
        md = row["metadata"]
        tid = md["instance_id"]
        if tid not in want:
            continue
        peaks = want.pop(tid)
        d = out_root / tid
        if (d / "instruction.md").exists() and not args.force:
            skipped.append(tid)
            continue
        tm = md["tmax"]
        if tm.get("fixtures"):
            missing.append(f"{tid} (unexpected fixtures)")
            continue
        if not args.apply:
            written.append(tid)
            continue
        (d / "environment").mkdir(parents=True, exist_ok=True)
        (d / "tests").mkdir(exist_ok=True)
        (d / "solution").mkdir(exist_ok=True)
        (d / "instruction.md").write_text(md["problem_statement"])
        workdir = md.get("workdir") or "/home/user"
        (d / "environment/Dockerfile").write_text(
            f"FROM {md['image']}\nWORKDIR {workdir}\n")
        (d / "tests/test.sh").write_text(tm["test_sh"])
        (d / "solution/solve.sh").write_text(STUB_SOLVE)
        env: dict[str, int] = {}
        ram = peaks.get("peak_ram_mb")
        if isinstance(ram, (int, float)) and ram > 2048:
            env["memory_mb"] = int(math.ceil(ram / 1024) + 1) * 1024
        if env:
            (d / "task.toml").write_text(
                "[environment]\n" +
                "".join(f"{k} = {v}\n" for k, v in env.items()))
        written.append(tid)
        print(f"materialized {tid}")

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "written": len(written), "skipped_existing": len(skipped),
        "problems": missing, "unresolved_ids": sorted(want),
        "inputs": {
            "train_parquet": {"path": str(train_parquet), "sha": _sha(train_parquet)},
            "prepared": {"path": str(prepared), "sha": _sha(prepared)},
        },
    }
    print(json.dumps({k: v for k, v in manifest.items() if k != "inputs"},
                     ensure_ascii=False))
    if not args.apply:
        print("dry run -- pass --apply to write")
        return
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root.parent / "materialize_manifest.json").write_text(
        json.dumps(manifest, indent=2))
    print(f"manifest -> {out_root.parent / 'materialize_manifest.json'}")


if __name__ == "__main__":
    main()
