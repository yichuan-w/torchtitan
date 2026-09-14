#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Cut the rows an offline evolution replaced into a paired evalset.

offline_publish.py writes a seed whose manifest lists the replaced tasks and
pins the parent seed by sha256. This takes both and writes two fixed sets of
the same tasks in the same order, the hardened rows and the rows they
replaced, so one policy scored on both (della/tb2_eval_local.sh with
SWE_TB2_DATA) says whether the rewrites are harder for it.

    offline_evalsets.py --seed <evolved seed jsonl> --out-dir <evalsets dir> \
        [--name <base name>]

Writes <name>-hardened.jsonl and <name>-original.jsonl under --out-dir, each
with a <name>-*.manifest.json in the shape of the other evalsets there
(name, rows, sha256, source, labels).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time


def _rows(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(path: str, rows: list[dict], *, name: str, source: str, note: str) -> str:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {
        "name": name,
        "rows": len(rows),
        "sha256": _sha256(path),
        "source": source,
        "source_rows": note,
        "created": time.strftime("%Y%m%d-%H%M%SZ", time.gmtime()),
        "labels": [r["metadata"]["instance_id"] for r in rows],
    }
    with open(path[: -len(".jsonl")] + ".manifest.json", "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return manifest["sha256"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument(
        "--seed", required=True, help="the evolved seed offline_publish.py wrote"
    )
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--name", help="base name; default: the seed's file stem")
    a = ap.parse_args()

    seed = os.path.abspath(a.seed)
    stem = seed[: -len(".jsonl")]
    with open(stem + ".manifest.json") as f:
        manifest = json.load(f)
    parent = manifest["parent_seed"]["path"]
    if _sha256(parent) != manifest["parent_seed"]["sha256"]:
        raise SystemExit(f"{parent} no longer matches the sha256 the manifest pinned")
    replaced = {r["task"] for r in manifest["replaced"]}

    hardened = [r for r in _rows(seed) if r["metadata"]["instance_id"] in replaced]
    by_id = {r["metadata"]["instance_id"]: r for r in _rows(parent)}
    original = [by_id[r["metadata"]["instance_id"]] for r in hardened]
    if len(hardened) != len(replaced):
        raise SystemExit(
            f"{len(hardened)} rows found for {len(replaced)} replaced tasks"
        )
    same = [r["metadata"]["instance_id"] for r, o in zip(hardened, original) if r == o]
    if same:
        raise SystemExit(
            f"{len(same)} replaced rows are identical to the originals: {same[:3]}"
        )

    name = a.name or os.path.basename(stem)
    os.makedirs(a.out_dir, exist_ok=True)
    for suffix, rows, source, note in (
        (
            "hardened",
            hardened,
            seed,
            f"the {len(replaced)} rows the manifest lists as replaced",
        ),
        (
            "original",
            original,
            parent,
            f"the same {len(replaced)} tasks, the rows the run trained on",
        ),
    ):
        path = os.path.join(a.out_dir, f"{name}-{suffix}.jsonl")
        sha = _write(path, rows, name=f"{name}-{suffix}", source=source, note=note)
        print(f"{path} rows={len(rows)} sha256={sha[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
