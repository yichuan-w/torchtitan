#!/usr/bin/env python3
"""Seed mix: Fzz1/SWE-Rebench-Tasks-Clean + latest Fzz1/Tmax-Tasks-Clean reaudit.

Tmax side is the current ``reaudit`` split (packages + integrity hook +
protected lists). Sandbox sizes come from the split's policy columns
``req_memory_mb`` / ``est_disk_mb`` (already ``ceil(peak * 1.3 / 1024)`` GiB
in MiB, 1 GiB floor, censored RAM already 6 GiB). Do not apply 1.3 again.

Rebench side is every shipped task package. Sizes come from
``peak_ram_mb`` / ``peak_disk_mb`` with the same 1.3 / 1 GiB floor / 8+10 cap
rule. ``--inject-agent-runtime`` is on: these Dockerfiles are upstream images
and need tmux.

Output is a seed file for ``new_root.py --mix``, not a live mix. Does not
touch an existing experiment root.

    TRL_TT=$PWD PYTHONPATH=$PWD python evolution/build_mix_rebench_tmax.py \\
        --out /path/to/mix_rebench_tmax.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_mix_v2 as v2  # noqa: E402
import pack_to_dataset as pack  # noqa: E402
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402

from torchtitan.experiments.rl.examples.tmax.evolution.seed_resources import (  # noqa: E402
    DISK_CAP_GB,
    MEM_CAP_GB,
    policy_gib as _policy_gib,
)

# Andy's data tree. Live extracts under data/tmax-extract stay untouched.
ANDY_DATA = Path("/scratch/gpfs/TRIDAO/al9080/terminal-rl/data")
TMAX_REV = "a84676aa19ae12713492245ffac98559e572ea86"
REBENCH_REV = "a6d8f8ead159520d079f81fa07fcb1aaa2ff1b04"
TMAX_ROOT = ANDY_DATA / f"tmax-reaudit-{TMAX_REV[:8]}"
REBENCH_ROOT = ANDY_DATA / f"rebench-{REBENCH_REV[:8]}"


def rebench_rows(tasks: Path, parquet: Path) -> tuple[list[dict], list[str]]:
    import pyarrow.parquet as pq  # noqa: PLC0415

    t = pq.read_table(parquet)
    by_id = {t.column("instance_id")[i].as_py(): i for i in range(t.num_rows)}
    cols = {c: t.column(c) for c in t.column_names}
    rows, missing = [], []
    for src in sorted(p for p in tasks.iterdir() if p.is_dir()):
        tid = src.name
        if not (src / "instruction.md").exists():
            missing.append(tid)
            continue
        try:
            row = pack.to_row(str(src), inject_agent_runtime=True)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{tid} (pack: {type(e).__name__}: {e})")
            continue
        md = row["metadata"]
        md["corpus"] = "swe-rebench"
        i = by_id.get(tid)
        if i is not None:
            cat = cols["tb_category"][i].as_py()
            if cat:
                md["terminal_domain"] = cat
            mem = v2._measured_gib(cols["peak_ram_mb"][i].as_py(), MEM_CAP_GB)
            if mem:
                md["daytona_mem_gb"] = mem
            dsk = v2._measured_gib(cols["peak_disk_mb"][i].as_py(), DISK_CAP_GB)
            if dsk:
                md["daytona_disk_gb"] = dsk
        rows.append(row)
    return rows, missing


def tmax_rows(tasks: Path, reaudit_parquet: Path) -> tuple[list[dict], list[str]]:
    """Same adapter as build_mix_v2, sizes from the current policy columns."""
    import pyarrow.parquet as pq  # noqa: PLC0415

    rows, missing = v2.tmax_rows(tasks, reaudit_parquet, None)
    t = pq.read_table(reaudit_parquet)
    policy = {}
    mem_col = t.column("req_memory_mb") if "req_memory_mb" in t.column_names else None
    disk_col = t.column("est_disk_mb") if "est_disk_mb" in t.column_names else None
    for i in range(t.num_rows):
        tid = t.column("task_id")[i].as_py()
        policy[tid] = {
            "mem": mem_col[i].as_py() if mem_col is not None else None,
            "disk": disk_col[i].as_py() if disk_col is not None else None,
        }
    for row in rows:
        md = row["metadata"]
        md["corpus"] = "tmax"
        pol = policy.get(md["instance_id"], {})
        mem = _policy_gib(pol.get("mem"), MEM_CAP_GB)
        if mem:
            md["daytona_mem_gb"] = mem
        dsk = _policy_gib(pol.get("disk"), DISK_CAP_GB)
        if dsk:
            md["daytona_disk_gb"] = dsk
    return rows, missing


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--rebench-tasks", type=Path, default=REBENCH_ROOT / "extract" / "tasks"
    )
    ap.add_argument(
        "--rebench-parquet",
        type=Path,
        default=REBENCH_ROOT / "clean" / "metadata" / "tasks.parquet",
    )
    ap.add_argument(
        "--tmax-tasks", type=Path, default=TMAX_ROOT / "tmax-extract" / "tasks"
    )
    ap.add_argument(
        "--tmax-parquet",
        type=Path,
        default=TMAX_ROOT / "tmax-clean" / "splits" / "reaudit.parquet",
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--holdout-n", type=int, default=64)
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rb, rb_missing = rebench_rows(args.rebench_tasks, args.rebench_parquet)
    tm, tm_missing = tmax_rows(args.tmax_tasks, args.tmax_parquet)
    rows = rb + tm
    rng = random.Random(args.seed)
    rng.shuffle(rows)

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": "swe-rebench + tmax-reaudit",
        "rebench_rows": len(rb),
        "rebench_missing": rb_missing,
        "rebench_revision": REBENCH_REV,
        "tmax_rows": len(tm),
        "tmax_missing_package": tm_missing,
        "tmax_revision": TMAX_REV,
        "tmax_hooked": sum(1 for r in tm if r["metadata"]["tmax"].get("pre_test_sh")),
        "total": len(rows),
        "holdout_n": args.holdout_n,
        "shuffle_seed": args.seed,
        "inputs": {
            "rebench_tasks": {
                "path": str(args.rebench_tasks),
                "sha": v2._sha_tree(args.rebench_tasks),
            },
            "rebench_parquet": {
                "path": str(args.rebench_parquet),
                "sha": v2._sha(args.rebench_parquet),
            },
            "tmax_tasks": {
                "path": str(args.tmax_tasks),
                "sha": v2._sha_tree(args.tmax_tasks),
            },
            "tmax_parquet": {
                "path": str(args.tmax_parquet),
                "sha": v2._sha(args.tmax_parquet),
            },
        },
    }
    print(
        json.dumps(
            {k: v for k, v in manifest.items() if k != "inputs"},
            ensure_ascii=False,
            indent=2,
        )
    )
    if rb_missing or tm_missing:
        print(
            f"WARNING: {len(rb_missing)} rebench + {len(tm_missing)} tmax unresolved",
            file=sys.stderr,
        )
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
