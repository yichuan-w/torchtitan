#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Fold rewrites the loop had accepted but never folded.

Older loops folded only when a round ended, so a round stopped early left its
accepted packages `running` (then `interrupted`, from finalize) with the
accepted package still under rewrites/<stamp>/package/. This finds every
loop.log line that says `-> accepted` whose rewrite was never folded and folds
them with the loop's own fold(): package -> r<N+1>, row rebuilt, a new mix
version, the lineage line. The current loop folds on acceptance, so this is
for roots written by an older one. Run with TRL_BASE set.

    offline_recover.py [--dry]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evolve_ondella as eo  # noqa: E402
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402

ACCEPTED = re.compile(
    r"INFO (\S+) (\S+) r(\d+) harder \S+ -> accepted \(\S+\) (tasks/\S+/rewrites/\S+)"
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    root = layout.Root.from_env()
    todo = []
    for line in open(root.evolution.loop_log, errors="replace"):
        m = ACCEPTED.search(line)
        if not m:
            continue
        tid, rev, rel = m.group(1), int(m.group(3)), m.group(4)
        rw_path = root.evolution.path / rel
        meta_path = rw_path / "rewrite.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("status") == "accepted" or meta.get("result_rev"):
            continue
        if root.evolution.task(tid).rev(rev + 1).exists():
            print(f"skip {tid}: r{rev + 1} already exists")
            continue
        if not (rw_path / "package" / "instruction.md").exists():
            print(f"skip {tid}: no package under {rel}")
            continue
        todo.append((tid, rw_path, meta))
    print(f"{len(todo)} accepted-but-unfolded rewrites")
    for tid, rw_path, meta in todo:
        print(f"  {tid} {rw_path.name} status={meta.get('status')}")
    if a.dry or not todo:
        return
    handled = []
    for tid, rw_path, meta in todo:
        rewrite = layout.RewriteDir(rw_path)
        meta["status"] = "accepted"
        meta.pop("error", None)
        meta.pop("stopped_loop_pid", None)
        layout.write_json_atomic(rewrite.meta, meta)
        handled.append(
            {"signal": None, "rewrite": rewrite, "meta": meta, "status": "accepted"}
        )
    version = eo.fold(root, handled)
    print(
        "folded -> mix v%s" % version, {h["meta"]["task"]: h["status"] for h in handled}
    )


if __name__ == "__main__":
    main()
