#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Copy an experiment root's live mix out as a named seed file, with a
manifest that says where every replaced row came from.

    offline_publish.py --root <root> --out <dir>/<name>.jsonl

The seed file is a byte copy of <root>/data/mix/live.jsonl; the manifest
beside it records the root, its live mix version and sha256, the seed mix the
root was built from, and for each row at rev > 0 the rewrite that produced it
(task, rev, rewrite dir, operator, signal, solved/graded). Rows at rev 0 are
the original seed rows, byte for byte.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = a.root.rstrip("/")
    live = os.path.join(root, "data", "mix", "live.jsonl")
    hist_dir = os.path.join(root, "data", "mix", "history")
    live_ver = sorted(f for f in os.listdir(hist_dir) if f.endswith(".jsonl"))[-1]
    manifest_live = json.load(
        open(os.path.join(hist_dir, live_ver.replace(".jsonl", ".manifest.json")))
    )
    exp = json.load(open(os.path.join(root, "experiment.json")))
    rows = [json.loads(l) for l in open(live) if l.strip()]
    replaced = []
    for r in rows:
        md = r["metadata"]
        rev = md.get("rev", 0)
        if not rev:
            continue
        tid = md["instance_id"]
        lineage = [
            json.loads(l)
            for l in open(os.path.join(root, "evolution/tasks", tid, "lineage.jsonl"))
        ]
        fold = [
            e for e in lineage if e.get("event") == "fold" and e.get("to_rev") == rev
        ]
        rw = fold[-1]["rewrite"] if fold else None
        meta = (
            json.load(
                open(os.path.join(root, "evolution/tasks", tid, rw, "rewrite.json"))
            )
            if rw
            else {}
        )
        replaced.append(
            {
                "task": tid,
                "rev": rev,
                "rewrite": f"evolution/tasks/{tid}/{rw}",
                "operator": meta.get("operator"),
                "harder_mode": meta.get("harder_mode"),
                "signal": meta.get("signal"),
                "solved": meta.get("solved"),
                "graded": meta.get("graded"),
            }
        )
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    shutil.copyfile(live, a.out)
    sha = hashlib.sha256(open(a.out, "rb").read()).hexdigest()
    assert sha == manifest_live["sha256"], (sha, manifest_live["sha256"])
    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sha256": sha,
        "rows": len(rows),
        "root": root,
        "mix_version": manifest_live["version"],
        "mix_file": f"data/mix/history/{live_ver}",
        "parent_seed": exp["seed_mix"],
        "purpose": exp["purpose"],
        "replaced_rows": len(replaced),
        "unchanged_rows": len(rows) - len(replaced),
        "replaced": replaced,
    }
    json.dump(manifest, open(a.out.replace(".jsonl", ".manifest.json"), "w"), indent=1)
    print(
        f"wrote {a.out} ({len(rows)} rows, {len(replaced)} replaced, sha256 {sha[:12]}) + manifest"
    )


if __name__ == "__main__":
    main()
