#!/usr/bin/env python3
"""Build the take8 restart mix: clean TerminalWorld seeds + Tmax-Tasks-Clean.

The 08-29 reset: the take7 mix had been through a budget backfill, a strip,
resource edits and months of in-place evolution folds -- not a clean
experiment start. This builds a fresh mix from first sources only:

  TW side    metadata/train_ready_ids.txt (669) -- the seed-cleanliness
             standard (oracle-passed, no infra errors, correct metadata).
             Rows are packed fresh from the SEED pool
             (data/sources/tw-extract/tasks), never from evolution output. Measured per-task disk overrides
             (results/disk_full.jsonl, real-block-usage semantics) are applied
             to these seed rows only, capped at Daytona's 10GB.

  Tmax side  Fzz1/Tmax-Tasks-Clean `train` split task_ids (400, rubric-audited
             + gpt-5.6-solved), joined against the already-prepared
             data/tmax_train.jsonl rows (which carry the verifier + fixtures).
             Rows gain daytona_mem_gb/daytona_disk_gb from the dataset's own
             measured peaks when above fleet defaults.

Output: rows shuffled with a fixed seed; the LAST --holdout-n rows are the
held-out validation slice (same convention as take7). A manifest,
`<out stem>.manifest.json` beside the output (the name new_root.py looks for
and records in experiment.json), pins counts, input digests and any id that
failed to resolve. The output is a seed file for `new_root.py --mix`, written
through layout.write_mix; it is not a root's live mix. Run on della.

    python3.11 build_mix_v2.py --out mix_v2.jsonl [--apply]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pack_to_dataset as pack  # noqa: E402
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402

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


def tw_rows(ids_path: Path, disk_results: Path, tasks_parquet: Path,
            seed_pool: Path) -> tuple[list[dict], list[str]]:
    import pyarrow.parquet as pq  # noqa: PLC0415

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
    for tid in (l.strip() for l in open(ids_path)):
        if not tid:
            continue
        src = seed_pool / tid
        if not src.is_dir():
            missing.append(tid)
            continue
        try:
            row = pack.to_row(str(src))
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
    ap.add_argument("--tw-ids", default=None,
                    help="default: the TW dataset's metadata/train_ready_ids.txt under "
                         "$TRL_BASE/data/sources/tw-extract")
    ap.add_argument("--tmax-parquet", default=None,
                    help="default: $TRL_BASE/data/sources/tmax-clean/splits/train.parquet")
    ap.add_argument("--tmax-prepared", default=None,
                    help="prepare_rts_data output; default: $TRL_BASE/data/tmax_train.jsonl")
    ap.add_argument("--disk-results", default=None,
                    help="measure_disk.py output; default: $TRL_BASE/results/disk_full.jsonl")
    ap.add_argument("--tasks-parquet", default=None,
                    help="default: the TW dataset's metadata/tasks.parquet under "
                         "$TRL_BASE/data/sources/tw-extract")
    ap.add_argument("--out", required=True, type=Path,
                    help="the seed file for new_root.py --mix")
    ap.add_argument("--holdout-n", type=int, default=64)
    ap.add_argument("--seed", type=int, default=1208)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = layout.Root.from_env()
    tw_src = root.data / "sources" / "tw-extract"
    tw_ids = Path(args.tw_ids) if args.tw_ids else tw_src / "metadata" / "train_ready_ids.txt"
    tasks_parquet = (Path(args.tasks_parquet) if args.tasks_parquet
                     else tw_src / "metadata" / "tasks.parquet")
    tmax_parquet = (Path(args.tmax_parquet) if args.tmax_parquet
                    else root.data / "sources" / "tmax-clean" / "splits" / "train.parquet")
    tmax_prepared = (Path(args.tmax_prepared) if args.tmax_prepared
                     else root.data / "tmax_train.jsonl")
    disk_results = (Path(args.disk_results) if args.disk_results
                    else root.path / "results" / "disk_full.jsonl")

    tw, tw_missing = tw_rows(tw_ids, disk_results, tasks_parquet, tw_src / "tasks")
    tm, tm_missing = tmax_rows(tmax_parquet, tmax_prepared)
    rows = tw + tm
    rng = random.Random(args.seed)
    rng.shuffle(rows)

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tw_rows": len(tw), "tw_missing": tw_missing,
        "tmax_rows": len(tm), "tmax_missing_prepared": tm_missing,
        "total": len(rows), "holdout_n": args.holdout_n, "shuffle_seed": args.seed,
        "inputs": {
            "tw_ids": {"path": str(tw_ids), "sha": _sha(tw_ids)},
            "tasks_parquet": {"path": str(tasks_parquet), "sha": _sha(tasks_parquet)},
            "tmax_parquet": {"path": str(tmax_parquet), "sha": _sha(tmax_parquet)},
            "tmax_prepared": {"path": str(tmax_prepared), "sha": _sha(tmax_prepared)},
            # The measured-disk file decides every TW daytona_disk_gb override.
            # Left unpinned, a build that silently ran without it is
            # indistinguishable afterwards from one that used it: the rows just
            # carry no disk override, which also happens when nothing measured
            # over the fleet default.
            "disk_results": {
                "path": str(disk_results),
                "sha": _sha(disk_results) if disk_results.exists() else None,
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
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    layout.write_mix(out, [json.dumps(r) for r in rows])
    manifest_path = layout.MixDir.manifest_of(out)
    layout.write_json_atomic(manifest_path, manifest)
    print(f"wrote {out} ({len(rows)} rows) + {manifest_path.name}")


if __name__ == "__main__":
    main()
