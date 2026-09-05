#!/usr/bin/env python3
"""Strip comment lines that sit inside a Dockerfile RUN continuation.

BuildKit tolerates them; Daytona's parser does not -- the comment terminates the
continuation and the next line is read as a new instruction, so the build dies
with `dockerfile parse error: unknown instruction: <word>`. The failure is
deterministic, which is why an affected task fails every single time it is
scheduled (one live task burned 448 builds in a single training boot before this
was found).

Two places carry a copy and BOTH must be fixed: the source package
(`data/sources/*-extract/tasks/<id>/environment/Dockerfile`, used by future
folds) and the live training row (`metadata.dockerfile` in the live mix, used
right now; training hot-reloads the file, so the fix takes effect without a
restart).

Default is a dry run. Pass --apply to write: each edited Dockerfile is backed up
beside itself first, and the mix goes through layout.write_mix, which on a
root's live mix publishes the next version -- the history is the backup.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout

POOLS = ("tw-extract", "swe-extract")


def offending_lines(text: str) -> list[int]:
    """1-based line numbers of comments inside a RUN continuation."""
    hits: list[int] = []
    cont = False
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if cont and s.startswith("#"):
            hits.append(i)
        if s and not s.startswith("#"):
            cont = s.endswith("\\")
    return hits


def strip(text: str) -> str:
    bad = set(offending_lines(text))
    if not bad:
        return text
    kept = [ln for i, ln in enumerate(text.splitlines(), 1) if i not in bad]
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def dockerfile_of(task_dir: Path) -> Path | None:
    for cand in (task_dir / "environment" / "Dockerfile", task_dir / "Dockerfile"):
        if cand.exists():
            return cand
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--mix", default=None, help="default: $TRL_BASE/data/mix/live.jsonl")
    args = ap.parse_args()
    root = layout.Root.from_env()
    mix = Path(args.mix) if args.mix else root.mix.live

    pkg_fixed = []
    for pool in POOLS:
        for task_dir in sorted((root.data / "sources" / pool / "tasks").glob("*/")):
            df = dockerfile_of(task_dir)
            if df is None:
                continue
            text = df.read_text(errors="replace")
            hits = offending_lines(text)
            if not hits:
                continue
            pkg_fixed.append((task_dir.name, len(hits)))
            if args.apply:
                shutil.copy2(df, str(df) + ".bak-inrun")
                df.write_text(strip(text))
    print(f"packages with in-RUN comments: {len(pkg_fixed)}")
    for tid, n in pkg_fixed[:15]:
        print(f"    {tid} ({n} lines)")
    if len(pkg_fixed) > 15:
        print(f"    ... {len(pkg_fixed) - 15} more")

    rows = [json.loads(l) for l in mix.read_text().splitlines() if l.strip()]
    live_fixed = []
    for r in rows:
        df = (r.get("metadata") or {}).get("dockerfile")
        if not isinstance(df, str):
            continue
        hits = offending_lines(df)
        if hits:
            live_fixed.append((r["label"], len(hits)))
            if args.apply:
                r["metadata"]["dockerfile"] = strip(df)
    print(f"live mix rows affected: {len(live_fixed)} of {len(rows)}")
    for tid, n in live_fixed:
        print(f"    {tid} ({n} lines)")

    if args.apply and live_fixed:
        published = layout.write_mix(mix, [json.dumps(r, ensure_ascii=False) for r in rows])
        print(f"mix rewritten ({len(rows)} rows preserved)"
              + (f"; published mix v{published[0]:04d}" if published else ""))
    elif not args.apply:
        print("dry run -- pass --apply to write")


if __name__ == "__main__":
    main()
