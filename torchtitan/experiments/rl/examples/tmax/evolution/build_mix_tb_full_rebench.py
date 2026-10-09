#!/usr/bin/env python3
"""Mix tb-full-data/tmax_clean prepared rows with all clean Rebench packages.

Run with --sources pointing to downloaded dataset directories and --out to a
fresh JSONL. The last --holdout-n rows are validation, matching TMaxDataset.
Tmax uses the release's prepared row and verified replay allocations (already
sized; no second headroom multiplier). Rebench uses the existing package adapter.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import random
import tarfile
from pathlib import Path

import pyarrow.parquet as pq

import build_mix_rebench_tmax as mix
import pack_to_dataset as pack

REVISIONS = {
    "tb-full-data": "ded8a2a591c8ad181f8626ed60f08400db7370a3",
    "SWE-Rebench-Tasks-Clean": "a6d8f8ead159520d079f81fa07fcb1aaa2ff1b04",
}


def checked_extract(archive: Path, destination: Path) -> dict[str, str]:
    """Validate plain task files and fingerprint packages before extraction."""
    digests = {}
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        groups = {}
        seen = set()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or path.parts[0] != "tasks":
                raise ValueError(f"unexpected archive path: {member.name}")
            if member.isdir():
                continue
            if not member.isfile() or len(path.parts) < 3 or member.name in seen:
                raise ValueError(f"unexpected archive member: {member.name}")
            seen.add(member.name)
            groups.setdefault(path.parts[1], []).append(member)
        for tid, files in groups.items():
            h = hashlib.sha256()
            for member in sorted(files, key=lambda m: m.name):
                h.update(str(Path(member.name).relative_to(Path("tasks") / tid)).encode())
                h.update(b"\0")
                h.update(tf.extractfile(member).read())
                h.update(b"\0")
            digests[tid] = h.hexdigest()
        if destination.exists():
            actual = {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()}
            if actual != seen:
                raise ValueError(f"incomplete or unexpected existing extraction: {destination}")
            for member in members:
                if member.isfile() and (destination / member.name).read_bytes() != tf.extractfile(member).read():
                    raise ValueError(f"existing extraction differs: {member.name}")
        else:
            tf.extractall(destination, filter="data")
    return digests


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--holdout-n", type=int, default=64)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    tb = args.sources / "tb-full-data"
    rb = args.sources / "SWE-Rebench-Tasks-Clean"
    extracts = args.out.parent / (args.out.stem + "-packages")
    hashes = checked_extract(tb / "data/tasks-tmax_clean.tar", extracts / "tmax")
    rb_hashes = checked_extract(rb / "data/tasks-00000.tar", extracts / "rebench")
    source_rows = pq.read_table(tb / "data/tmax_clean.parquet").to_pylist()
    ids = [r["task_id"] for r in source_rows]
    assert len(ids) == len(set(ids)) and set(ids) == set(hashes)
    rows = []
    for source in source_rows:
        tid = source["task_id"]
        assert source["split"] == "tmax_clean"
        assert source["tier"] == "SEED" and source["verdict"] == "CLEAN"
        assert source["solution_replay_reward"] == 1
        assert source["task_content_sha256"] == hashes[tid], tid
        row = json.loads(source["prepared_row_json"])
        assert set(row) == {"prompt", "label", "metadata"}
        md = row["metadata"]
        assert md["instance_id"] == tid and md["tmax"]["test_sh"]
        # A future release carrying hooks must not silently lose them.
        if source["pre_test_sh"]:
            assert md["tmax"].get("pre_test_sh") == source["pre_test_sh"]
        protected = pack.Protected.from_cells(source["protected_paths"], source["protected_cmds"])
        if protected is not None:
            md["tmax"].update(pack._ib_module().tmax_protected_fields(protected.paths, protected.cmds))
        allocation = json.loads(source["solution_replay_resources_json"])
        for key, field, cap in [("daytona_cpu", "cpu_applied", 4),
                                ("daytona_mem_gb", "memory_gib", 8),
                                ("daytona_disk_gb", "disk_gib", 10)]:
            value = allocation[field]
            assert isinstance(value, (int, float)) and value == int(value) and 1 <= value <= cap
            md[key] = int(value)
        md.update(corpus="tmax", source_dataset="Fzz1/tb-full-data",
                  source_split="tmax_clean", source_revision=REVISIONS["tb-full-data"],
                  tmax_domain=source["tmax_domain"])
        rows.append(row)
    rebench_ids = pq.read_table(rb / "metadata/tasks.parquet", columns=["instance_id"])["instance_id"].to_pylist()
    assert len(rebench_ids) == len(set(rebench_ids)) and set(rebench_ids) == set(rb_hashes)
    rebench, missing = mix.rebench_rows(extracts / "rebench/tasks", rb / "metadata/tasks.parquet")
    if missing:
        raise ValueError(f"Rebench conversion failed: {missing}")
    for row in rebench:
        row["metadata"].update(source_dataset="Fzz1/SWE-Rebench-Tasks-Clean",
                               source_split="train", source_revision=REVISIONS["SWE-Rebench-Tasks-Clean"])
    rows.extend(rebench)
    assert len({r["metadata"]["instance_id"] for r in rows}) == len(rows)
    assert 0 <= args.holdout_n < len(rows)
    random.Random(args.seed).shuffle(rows)
    train = rows[:-args.holdout_n] if args.holdout_n else rows
    holdout = rows[-args.holdout_n:] if args.holdout_n else []
    def counts(group):
        return dict(Counter(r["metadata"]["corpus"] for r in group))
    def write(path, group):
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in group))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write(args.out, rows)
    write(args.out.with_suffix(".train.jsonl"), train)
    write(args.out.with_suffix(".holdout.jsonl"), holdout)
    inputs = [tb / "data/tmax_clean.parquet", tb / "data/tasks-tmax_clean.tar",
              rb / "metadata/tasks.parquet", rb / "data/tasks-00000.tar"]
    manifest = {"total": len(rows), "counts": counts(rows), "train": counts(train),
                "holdout": counts(holdout), "holdout_n": args.holdout_n, "seed": args.seed,
                "source_revisions": REVISIONS, "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                "output_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
                "resources": dict(Counter('/'.join(str(r['metadata'].get(k, 'fallback')) for k in
                  ['daytona_cpu', 'daytona_mem_gb', 'daytona_disk_gb']) for r in rows)),
                "resource_policy": "Tmax: verified solution replay allocations; Rebench: package CPU, measured RAM/disk x1.3, GiB ceil, caps 8/10",
                "validation": "package membership and Tmax content digests checked; all rows converted; no sandbox rollout executed"}
    args.out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "inputs"}, indent=2))


if __name__ == "__main__":
    main()
