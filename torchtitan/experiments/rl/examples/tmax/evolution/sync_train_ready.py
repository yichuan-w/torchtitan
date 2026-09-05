#!/usr/bin/env python3
"""Reconcile train_ready_ids.txt with the mix it is supposed to describe.

The list and the mix are edited by different tools. drop_from_mix.py takes a row
out of the live mix and writes removed_tasks.jsonl; it does not touch the id
list, so a dropped task stays on the list and walks back into the corpus at the
next build_mix_v2.py run -- the drop undone by a rebuild, with nothing to say it
happened. Repairs go the other way: a task fixed and verified is still off the
list, so the rebuild leaves it out.

This reports both directions and, with --apply, writes the list. It never edits
the mix: a row belongs to a rebuild, and this is the input a rebuild reads.

  sync_train_ready.py                       # report
  sync_train_ready.py --apply               # drop ids no longer in the mix
  sync_train_ready.py --add tw_693888 ...   # readmit repaired tasks
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout


def mix_ids(mix: Path) -> set[str]:
    out = set()
    with mix.open() as fh:
        for line in fh:
            if line.strip():
                out.add(json.loads(line)["metadata"]["instance_id"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default=None,
                    help="default: the TW dataset's metadata/train_ready_ids.txt under "
                         "$TRL_BASE/data/sources/tw-extract")
    ap.add_argument("--mix", default=None, help="default: $TRL_BASE/data/mix/live.jsonl")
    ap.add_argument("--add", nargs="*", default=[],
                    help="ids to readmit (a repaired task, verified)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = layout.Root.from_env()
    list_path = Path(a.list) if a.list else (root.data / "sources" / "tw-extract" / "metadata"
                                             / "train_ready_ids.txt")
    mix_path = Path(a.mix) if a.mix else root.mix.live
    ready = [ln.strip() for ln in list_path.read_text().splitlines() if ln.strip()]
    live = mix_ids(mix_path)

    # Only tw_ ids are this list's business. The mix also carries TMax rows,
    # which come from their own parquet and were never on it.
    stale = [t for t in ready if t.startswith("tw_") and t not in live]
    add = [t for t in a.add if t not in ready]
    already = [t for t in a.add if t in ready]

    print(f"list: {len(ready)} ids   mix: {len(live)} rows")
    for t in stale:
        print(f"  drop  {t}  -- on the list, no longer in the mix")
    for t in add:
        print(f"  add   {t}  -- readmitted")
    for t in already:
        print(f"  keep  {t}  -- already on the list")
    if not stale and not add:
        print("  nothing to change")
        return

    new = [t for t in ready if t not in set(stale)] + add
    if not a.apply:
        print(f"dry run -- would write {len(new)} ids; pass --apply")
        return
    backup = list_path.with_suffix(f".txt.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(list_path, backup)
    list_path.write_text("\n".join(new) + "\n")
    print(f"wrote {len(new)} ids; previous list kept at {backup.name}")


if __name__ == "__main__":
    main()
