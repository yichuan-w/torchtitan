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

  Tmax side  Fzz1/Tmax-Tasks-Clean `reaudit` split: one task package per
             task_id under data/sources/tmax-extract/tasks (the dataset's
             data/tasks-reaudit-*.tar, untarred there; the same directory the
             loop copies r0 from), packed through pack.to_row like the TW
             seeds, so a seed row and a folded row come off one adapter.
             The split's parquet carries the pin hook (pre_test_sh and the
             environment stamp) and the domain; the `reaudit_full` parquet
             carries the measured peaks that size daytona_mem_gb /
             daytona_disk_gb, and a task without a reading, or one read at
             the ceiling, keeps the fleet default.

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


def _sha_tree(root: Path) -> str:
    """A digest over every file under `root`, relative path and content in
    sorted order: pins a package directory in the manifest the way `_sha`
    pins a file."""
    h = hashlib.sha256()
    for p in sorted(q for q in root.rglob("*") if q.is_file()):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
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


def _column(table, name: str) -> list:
    """A column as Python values, or Nones when the file does not carry it:
    the hook columns are optional on the dataset."""
    if name not in table.schema.names:
        return [None] * table.num_rows
    return table.column(name).to_pylist()


def tmax_rows(tasks: Path, reaudit_parquet: Path, peaks_parquet: Path | None
              ) -> tuple[list[dict], list[str]]:
    """TMax rows from the reaudit task packages, one per task_id in the split.

    `tasks` holds the packages (data/sources/tmax-extract/tasks, the loop's
    r0 source), so a seed row and a folded row are one adapter over the same
    bytes. `reaudit_parquet` names the tasks and carries the domain and the
    pin hook, which goes on the row through pack.to_row beside this package's
    own environment identity. `peaks_parquet` (the reaudit_full split) carries
    the measured peaks; None sizes nothing. Returns (rows, ids with no
    package).
    """
    import pyarrow.parquet as pq  # noqa: PLC0415

    t = pq.read_table(reaudit_parquet)
    ids = t.column("task_id").to_pylist()
    domain = _column(t, "terminal_domain")
    hook_sh = _column(t, "pre_test_sh")
    hook_env = _column(t, "pre_test_env_identity")

    peaks: dict[str, dict] = {}
    if peaks_parquet is not None:
        p = pq.read_table(peaks_parquet, columns=[
            "task_id", "peak_ram_mb", "peak_disk_mb", "ram_at_ceiling", "disk_at_ceiling"])
        for i in range(p.num_rows):
            peaks[p.column("task_id")[i].as_py()] = {
                "peak_ram_mb": p.column("peak_ram_mb")[i].as_py(),
                "peak_disk_mb": p.column("peak_disk_mb")[i].as_py(),
                "at_ceiling": bool(p.column("ram_at_ceiling")[i].as_py()
                                   or p.column("disk_at_ceiling")[i].as_py()),
            }

    rows, missing = [], []
    for i, tid in enumerate(ids):
        src = tasks / tid
        if not (src / "instruction.md").exists():
            missing.append(tid)
            continue
        hook = (str(hook_sh[i]), str(hook_env[i] or "")) if hook_sh[i] else None
        try:
            row = pack.to_row(str(src), pretest=hook)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{tid} (pack: {type(e).__name__})")
            continue
        md = row["metadata"]
        if domain[i]:
            md["terminal_domain"] = domain[i]
        meta = peaks.get(tid)
        # A ceiling flag means the reading is the cap, not the requirement, so
        # sizing from it would provision off a truncated number. Leave those to
        # the fleet default until an un-pressured run exists.
        if meta and not meta["at_ceiling"]:
            mem = _measured_gib(meta["peak_ram_mb"], 8)
            if mem:
                md["daytona_mem_gb"] = mem
            dsk = _measured_gib(meta["peak_disk_mb"], DISK_CAP_GB)
            if dsk:
                md["daytona_disk_gb"] = dsk
        rows.append(row)
    return rows, missing


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tw-ids", default=None,
                    help="default: the TW dataset's metadata/train_ready_ids.txt under "
                         "$TRL_BASE/data/sources/tw-extract")
    ap.add_argument("--tmax-tasks", default=None,
                    help="the reaudit task packages, one directory per task_id; default: "
                         "$TRL_BASE/data/sources/tmax-extract/tasks (the loop's r0 source)")
    ap.add_argument("--tmax-parquet", default=None,
                    help="the reaudit split: task ids, domain and the pin hook; default: "
                         "$TRL_BASE/data/sources/tmax-clean/splits/reaudit.parquet")
    ap.add_argument("--tmax-peaks", default=None,
                    help="the reaudit_full split, with the measured peaks that size the "
                         "rows; default: $TRL_BASE/data/sources/tmax-clean/splits/"
                         "reaudit_full.parquet. Missing = every tmax row at the fleet default")
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
    tmax_clean = root.data / "sources" / "tmax-clean" / "splits"
    tmax_tasks = (Path(args.tmax_tasks) if args.tmax_tasks
                  else root.data / "sources" / "tmax-extract" / "tasks")
    tmax_parquet = (Path(args.tmax_parquet) if args.tmax_parquet
                    else tmax_clean / "reaudit.parquet")
    tmax_peaks = (Path(args.tmax_peaks) if args.tmax_peaks
                  else tmax_clean / "reaudit_full.parquet")
    disk_results = (Path(args.disk_results) if args.disk_results
                    else root.path / "results" / "disk_full.jsonl")
    if not tmax_peaks.exists():
        print(f"WARNING: {tmax_peaks} missing -- every tmax row falls back to the "
              f"fleet default size", file=sys.stderr)

    tw, tw_missing = tw_rows(tw_ids, disk_results, tasks_parquet, tw_src / "tasks")
    tm, tm_missing = tmax_rows(tmax_tasks, tmax_parquet,
                               tmax_peaks if tmax_peaks.exists() else None)
    rows = tw + tm
    rng = random.Random(args.seed)
    rng.shuffle(rows)

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tw_rows": len(tw), "tw_missing": tw_missing,
        "tmax_rows": len(tm), "tmax_missing_package": tm_missing,
        "tmax_hooked": sum(1 for r in tm if r["metadata"]["tmax"].get("pre_test_sh")),
        "total": len(rows), "holdout_n": args.holdout_n, "shuffle_seed": args.seed,
        "inputs": {
            "tw_ids": {"path": str(tw_ids), "sha": _sha(tw_ids)},
            "tasks_parquet": {"path": str(tasks_parquet), "sha": _sha(tasks_parquet)},
            "tmax_tasks": {"path": str(tmax_tasks), "sha": _sha_tree(tmax_tasks)},
            "tmax_parquet": {"path": str(tmax_parquet), "sha": _sha(tmax_parquet)},
            "tmax_peaks": {"path": str(tmax_peaks),
                           "sha": _sha(tmax_peaks) if tmax_peaks.exists() else None},
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
