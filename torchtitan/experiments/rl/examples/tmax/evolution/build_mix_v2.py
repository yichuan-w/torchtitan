#!/usr/bin/env python3
"""Build the take8 restart mix: clean TerminalWorld seeds + Tmax-Tasks-Clean.

The 08-29 reset: the take7 mix had been through a budget backfill, a strip,
resource edits and months of in-place evolution folds -- not a clean
experiment start. This builds a fresh mix from first sources only:

  TW side    metadata/train_ready_ids.txt (669) -- the seed-cleanliness
             standard (oracle-passed, no infra errors, correct metadata).
             Rows are packed fresh from the SEED pool (data/tw-extract/tasks),
             never from evolution output. Measured per-task disk overrides
             (results/disk_full.jsonl, real-block-usage semantics) are applied
             to these seed rows only, capped at Daytona's 10GB.

  Tmax side  Fzz1/Tmax-Tasks-Clean `train` split task_ids (400, rubric-audited
             + gpt-5.6-solved), joined against the already-prepared
             data/tmax_train.jsonl rows (which carry the verifier + fixtures).
             Rows gain daytona_mem_gb/daytona_disk_gb from the dataset's own
             measured peaks when above fleet defaults.

Output: rows shuffled with a fixed seed; the LAST --holdout-n rows are the
held-out validation slice (same convention as take7). A manifest records
counts, input digests and any id that failed to resolve. Run on della.

    python3.11 build_mix_v2.py --out $ROOT/data/mix/mix_v2.jsonl [--apply]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("TRL_ROOT", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))
DISK_CAP_GB = 10
FLEET_MEM_GB, FLEET_DISK_GB = 2, 2


def _measured_gib(mb: float | None, cap: int) -> int | None:
    """Measured peak to a provisioned size: x1.3 headroom, floor 1 GiB."""
    if not isinstance(mb, (int, float)) or mb <= 0:
        return None
    return min(max(math.ceil(mb * 1.3 / 1024), 1), cap)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def tw_rows(ids_path: Path, disk_results: Path,
            tasks_parquet: Path) -> tuple[list[dict], list[str]]:
    sys.path.insert(0, str(ROOT / "evolve-onhost/scripts"))
    import pyarrow.parquet as pq  # noqa: PLC0415
    import solve_daytona as sd  # noqa: PLC0415 -- della-side import

    # The dataset's own per-task metadata is the source of truth
    # (metadata/tasks.parquet: req_cpus / req_memory_mb / est_disk_mb /
    # terminal_domain, per task, not template values).
    t = pq.read_table(tasks_parquet, columns=[
        "task_id", "req_cpus", "req_memory_mb", "est_disk_mb", "terminal_domain"])
    declared = {}
    for i in range(t.num_rows):
        declared[t.column("task_id")[i].as_py()] = {
            c: t.column(c)[i].as_py()
            for c in ("req_cpus", "req_memory_mb", "est_disk_mb", "terminal_domain")
        }

    disk_gb: dict[str, int] = {}
    if not disk_results.exists():
        print(f"WARNING: {disk_results} missing -- every measured disk override "
              f"is being dropped, rows fall back to est_disk_mb", file=sys.stderr)
    if disk_results.exists():
        for line in open(disk_results):
            r = json.loads(line)
            # Every measurement, not only the ones above the fleet default: a
            # task measured at 300MB and one measured at 1.9GB are different
            # facts, and thresholding throws the difference away.
            if r.get("built") and (r.get("recommend_daytona_gb") or 0) > 0:
                disk_gb[r["task_id"]] = min(
                    max(int(r["recommend_daytona_gb"]), 1), DISK_CAP_GB)

    rows, missing = [], []
    seed_pool = ROOT / "data/tw-extract/tasks"
    for tid in (l.strip() for l in open(ids_path)):
        if not tid:
            continue
        src = seed_pool / tid
        if not src.is_dir():
            missing.append(tid)
            continue
        try:
            row = sd.pack.to_row(str(src))
        except Exception as e:  # noqa: BLE001
            missing.append(f"{tid} (pack: {type(e).__name__})")
            continue
        md = row["metadata"]
        d = declared.get(tid, {})
        if d.get("terminal_domain"):
            md["terminal_domain"] = d["terminal_domain"]
        cpus = d.get("req_cpus")
        if isinstance(cpus, (int, float)) and cpus > 1:
            md["daytona_cpu"] = math.ceil(cpus)
        mem = d.get("req_memory_mb")
        if isinstance(mem, (int, float)) and mem > FLEET_MEM_GB * 1024:
            md["daytona_mem_gb"] = min(math.ceil(mem / 1024), 8)
        # measured real disk beats the est_ column; est is a floor with slack
        if tid in disk_gb:
            md["daytona_disk_gb"] = disk_gb[tid]
        else:
            est = d.get("est_disk_mb")
            if isinstance(est, (int, float)) and est > FLEET_DISK_GB * 1024:
                md["daytona_disk_gb"] = min(math.ceil(est / 1024), DISK_CAP_GB)
        rows.append(row)
    return rows, missing


def tmax_rows(parquet: Path, prepared: Path) -> tuple[list[dict], list[str]]:
    import pyarrow.parquet as pq  # noqa: PLC0415

    t = pq.read_table(parquet, columns=[
        "task_id", "tb_domain", "peak_ram_mb", "peak_disk_mb",
        "ram_at_ceiling", "disk_at_ceiling"])
    want = {}
    for i in range(t.num_rows):
        want[t.column("task_id")[i].as_py()] = {
            "tb_domain": t.column("tb_domain")[i].as_py(),
            "peak_ram_mb": t.column("peak_ram_mb")[i].as_py(),
            "peak_disk_mb": t.column("peak_disk_mb")[i].as_py(),
            "at_ceiling": bool(t.column("ram_at_ceiling")[i].as_py()
                               or t.column("disk_at_ceiling")[i].as_py()),
        }
    rows = []
    for line in open(prepared):
        row = json.loads(line)
        tid = row["metadata"]["instance_id"]
        if tid not in want:
            continue
        meta = want.pop(tid)
        md = row["metadata"]
        if meta["tb_domain"]:
            md["terminal_domain"] = meta["tb_domain"]
        # A ceiling flag means the reading is the cap, not the requirement, so
        # sizing from it would provision off a truncated number. Leave those to
        # the fleet default until an un-pressured run exists.
        if not meta["at_ceiling"]:
            mem = _measured_gib(meta["peak_ram_mb"], 8)
            if mem:
                md["daytona_mem_gb"] = mem
            dsk = _measured_gib(meta["peak_disk_mb"], DISK_CAP_GB)
            if dsk:
                md["daytona_disk_gb"] = dsk
        rows.append(row)
    return rows, sorted(want)  # leftovers = ids with no prepared row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tw-ids", default=str(ROOT / "data/mix/train_ready_ids.txt"))
    ap.add_argument("--tmax-parquet",
                    default=str(ROOT / "data/tmax-clean/splits/train.parquet"))
    ap.add_argument("--tmax-prepared", default=str(ROOT / "data/tmax_train.jsonl"))
    ap.add_argument("--disk-results", default=str(ROOT / "results/disk_full.jsonl"))
    ap.add_argument("--tasks-parquet",
                    default=str(ROOT / "data/mix/tasks.parquet"))
    ap.add_argument("--out", default=str(ROOT / "data/mix/mix_v2.jsonl"))
    ap.add_argument("--holdout-n", type=int, default=64)
    ap.add_argument("--seed", type=int, default=1208)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    tw, tw_missing = tw_rows(Path(args.tw_ids), Path(args.disk_results), Path(args.tasks_parquet))
    tm, tm_missing = tmax_rows(Path(args.tmax_parquet), Path(args.tmax_prepared))
    rows = tw + tm
    rng = random.Random(args.seed)
    rng.shuffle(rows)

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tw_rows": len(tw), "tw_missing": tw_missing,
        "tmax_rows": len(tm), "tmax_missing_prepared": tm_missing,
        "total": len(rows), "holdout_n": args.holdout_n, "shuffle_seed": args.seed,
        "inputs": {
            "tw_ids": {"path": args.tw_ids, "sha": _sha(Path(args.tw_ids))},
            "tasks_parquet": {"path": args.tasks_parquet,
                              "sha": _sha(Path(args.tasks_parquet))},
            "tmax_parquet": {"path": args.tmax_parquet,
                             "sha": _sha(Path(args.tmax_parquet))},
            "tmax_prepared": {"path": args.tmax_prepared,
                              "sha": _sha(Path(args.tmax_prepared))},
            # The measured-disk file decides every TW daytona_disk_gb override.
            # Left unpinned, a build that silently ran without it is
            # indistinguishable afterwards from one that used it: the rows just
            # carry no disk override, which also happens when nothing measured
            # over the fleet default.
            "disk_results": {
                "path": args.disk_results,
                "sha": (_sha(Path(args.disk_results))
                        if Path(args.disk_results).exists() else None),
            },
        },
    }
    print(json.dumps({k: v for k, v in manifest.items() if k != "inputs"},
                     ensure_ascii=False, indent=2))
    if tw_missing or tm_missing:
        print(f"WARNING: {len(tw_missing)} TW + {len(tm_missing)} tmax ids unresolved",
              file=sys.stderr)
    if not args.apply:
        print("dry run -- pass --apply to write")
        return
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    with open(str(out) + ".manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"wrote {out} ({len(rows)} rows) + manifest")


if __name__ == "__main__":
    main()
