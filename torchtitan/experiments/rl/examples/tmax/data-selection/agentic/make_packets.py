#!/usr/bin/env python
"""Turn the raw candidate dumps into agent-sized packets.

A raw candidate file carries ~60 candidates x 5 corpora with a 1400-char
snippet each -- ~420KB, far too much to put in front of an agent ten times
over. Each packet instead shows the top SHOW per corpus with a short snippet
and lists the remaining ids bare, so the agent can pull any of them with
`tools.py show` when it wants to.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=HERE / "work" / "candidates")
    ap.add_argument("--dst", type=Path, default=HERE / "work" / "packets")
    ap.add_argument("--show", type=int, default=20)
    ap.add_argument("--snippet", type=int, default=520)
    args = ap.parse_args()
    args.dst.mkdir(parents=True, exist_ok=True)

    sizes = []
    for src in sorted(args.src.glob("*.json")):
        if src.stem == "_meta":
            continue
        raw = json.loads(src.read_text())
        out = {
            "tb21_id": raw["tb21_id"],
            "tb21_instruction": raw["tb21_instruction"],
            "how_to_read": (
                "shown = top candidates by fused rank (tfidf_word/tfidf_char/bm25/"
                "dense). n_hits = how many of the 4 retrievers found it. rest_ids = "
                "further candidates, ids only -- expand with `tools.py show <id>`. "
                "Rank order here is retrieval, not truth; rerank it yourself and "
                "search the pool for anything the retrievers missed."
            ),
            "candidates": {},
        }
        for corpus, lst in raw["candidates"].items():
            shown = [
                {
                    "task_id": e["task_id"],
                    "n_hits": e["n_hits"],
                    "rrf": e["rrf"],
                    "found_by": sorted(e["signals"]),
                    "inst_chars": e["inst_chars"],
                    "snippet": " ".join(e["snippet"].split())[: args.snippet],
                }
                for e in lst[: args.show]
            ]
            out["candidates"][corpus] = {
                "n_total": len(lst),
                "shown": shown,
                "rest_ids": [e["task_id"] for e in lst[args.show :]],
            }
        dst = args.dst / src.name
        dst.write_text(json.dumps(out, indent=1))
        sizes.append(dst.stat().st_size)

    if sizes:
        sizes.sort()
        print(
            f"[packets] wrote {len(sizes)} files; "
            f"median {sizes[len(sizes) // 2] / 1024:.0f}KB, max {sizes[-1] / 1024:.0f}KB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
