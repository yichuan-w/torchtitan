#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Publish a mix version that restores per-task annotation keys a fold dropped.

Before 2026-09-14 the fold rebuilt a row from its package and carried only
the daytona_* sizes and the pin hook across, so an annotation that lives on
the row alone (terminal_domain, from the corpus table) was lost on every
rewritten row. This reads the live mix of a root, fills each named key that a
row lacks from the row of the same instance_id in --from (the parent seed),
and publishes the result as the next version through the layout, so the
history keeps both. Rows that already carry the key are left as they are.

    mix_carry_keys.py --root <root> --from <parent seed jsonl> --keys terminal_domain
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--root", required=True)
    ap.add_argument(
        "--from", dest="source", required=True, help="the seed holding the keys"
    )
    ap.add_argument("--keys", nargs="+", required=True)
    a = ap.parse_args()
    root = layout.Root(Path(a.root))
    source = {}
    with open(a.source) as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                source[row["metadata"]["instance_id"]] = row["metadata"]
    out, filled = [], {k: 0 for k in a.keys}
    for line in root.mix.live.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        md = row["metadata"]
        src = source.get(md["instance_id"], {})
        changed = False
        for k in a.keys:
            if k not in md and k in src:
                md[k] = src[k]
                filled[k] += 1
                changed = True
        out.append(json.dumps(row, ensure_ascii=False) if changed else line)
    if not any(filled.values()):
        print("nothing to fill")
        return 0
    version, path = root.mix.publish(out)
    print(f"filled {filled} -> mix v{version} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
