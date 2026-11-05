#!/usr/bin/env python3
"""Run the EVOLVE_GRADER_VALUE_LEAK check offline over a root's past rewrites.

    measure_grader_value_leak.py <root> [--job harder]

Read-only. For every rewrite with a package, compares it with the revision it
started from (evolution/tasks/<task>/r<input_rev>) and prints one JSON line per
flagged rewrite, then a summary line.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grader_value_leak as gvl  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--job", default="harder")
    args = ap.parse_args()
    seen = flagged = accepted = accepted_flagged = 0
    for meta_path in sorted((args.root / "evolution" / "tasks").glob("*/rewrites/*/rewrite.json")):
        rewrite = meta_path.parent
        if not rewrite.name.endswith("--" + args.job):
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        task = rewrite.parent.parent
        parent = task / f"r{meta.get('input_rev')}"
        new = rewrite / "package"
        if not new.is_dir() and meta.get("result_rev") is not None:
            new = task / f"r{meta['result_rev']}"
        if not parent.is_dir() or not new.is_dir():
            continue
        seen += 1
        is_accepted = meta.get("status") == "accepted"
        accepted += is_accepted
        verdict = gvl.check(parent, new)
        if not verdict["ok"]:
            flagged += 1
            accepted_flagged += is_accepted
            print(json.dumps({"rewrite": str(rewrite.relative_to(args.root)),
                              "status": meta.get("status"), "tokens": verdict["tokens"]}))
    print(json.dumps({"rewrites": seen, "flagged": flagged,
                      "accepted": accepted, "accepted_flagged": accepted_flagged}))


if __name__ == "__main__":
    main()
