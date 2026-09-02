#!/usr/bin/env python3
"""Remove a task from the live mix, without disturbing the held-out tail.

The last `holdout_n` rows are the frozen validation slice, and training serves
everything before them. Dropping a row from the head therefore leaves the
holdout membership unchanged -- the same 64 tasks are still the last 64 -- while
dropping one from inside it would pull a new task in and invalidate every
before/after comparison anchored on that set. This refuses to touch the tail.

Removing a task is the last resort and the reason belongs in the record, so
--why is required and is written into the sidecar log next to the mix.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

HOLDOUT_N = 64


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_ids", nargs="+")
    ap.add_argument("--mix",
                    default="/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/mix_live.jsonl")
    ap.add_argument("--why", required=True, help="why this task is being removed")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    mix = Path(a.mix)
    lines = [l for l in mix.read_text().splitlines() if l.strip()]
    ids = [json.loads(l)["metadata"]["instance_id"] for l in lines]
    tail = set(ids[-HOLDOUT_N:])
    print(f"mix: {len(lines)} rows, holdout = last {HOLDOUT_N}")

    drop = []
    for t in a.task_ids:
        if t not in ids:
            print(f"  {t}: not in the mix")
        elif t in tail:
            print(f"  {t}: IN THE HOLDOUT -- refusing; removing it would swap in a "
                  f"new validation task")
        else:
            drop.append(t)
            print(f"  {t}: row {ids.index(t)}, will be removed")
    if not drop:
        print("nothing to do")
        return
    if not a.apply:
        print(f"dry run -- {len(drop)} rows would go; pass --apply")
        return

    keep = [l for l, t in zip(lines, ids) if t not in set(drop)]
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    bak = f"{a.mix}.bak-drop-{stamp}"
    shutil.copy2(mix, bak)
    mix.write_text("\n".join(keep) + "\n")
    log = mix.parent / "removed_tasks.jsonl"
    with open(log, "a") as f:
        for t in drop:
            f.write(json.dumps({"task_id": t, "removed_at": stamp,
                                "why": a.why}, ensure_ascii=False) + "\n")
    new_ids = [json.loads(l)["metadata"]["instance_id"] for l in keep]
    print(f"{len(lines)} -> {len(keep)} rows; backup {bak}")
    print(f"holdout unchanged: {set(new_ids[-HOLDOUT_N:]) == tail}")
    print(f"reason logged to {log}")


if __name__ == "__main__":
    main()
