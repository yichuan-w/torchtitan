#!/usr/bin/env python3
"""Return repaired task packages to the live mix WITHOUT disturbing the holdout.

The last `holdout_n` rows of the live mix (file order) are the training
harness's held-out validation slice -- training serves rows[:-holdout_n] and the
frozen 64-task eval set is exactly that tail. Appending a new row therefore
pushes one task out of the holdout and pulls a new one in, which silently
invalidates every before/after eval comparison anchored on it.

So a re-entering task is inserted immediately BEFORE the holdout window. Tasks
already present are replaced in place (their position is kept). The evolve
loop's own fold only ever replaces existing ids, so it is not affected by this;
this script exists for the case the loop does not cover -- a task that was
purged from the mix, repaired, and is now coming back.

Dry run by default. --apply writes through layout.write_mix: on a root's live
mix that publishes the next version, and the history is the backup.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pack_to_dataset as pack  # noqa: E402
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402

HOLDOUT_N = int(os.environ.get("TMAX_HOLDOUT_N", "64"))


def find_package(root: layout.Root, task_id: str) -> Path | None:
    for pool in ("tw-extract", "swe-extract"):
        cand = root.data / "sources" / pool / "tasks" / task_id
        if cand.exists():
            return cand
    return None


# Written onto a row after it is folded, and not recoverable from the package.
# ``rev`` is the loop's: a signal names the rev it saw and the loop drops one
# about a rev the task has moved past, so a replaced row keeps its number.
_CARRIED_FIELDS = ("daytona_cpu", "daytona_mem_gb", "daytona_disk_gb",
                   "terminal_domain", "rev")


def partial_reference(pkg: Path) -> bool:
    """True when the package ships a reference solution that cannot pass.

    Some packages carry a deliberately incomplete solve.sh -- tw_158378's opens
    `# Partial: ... removes PR #2731 but NOT PR #2726`, written to simulate an
    agent that stopped halfway. Such a task can never satisfy cleanliness
    criterion 1, and 54 of the corpus's 55 are already out of train_ready.
    tw_158378 came back in through this script, because refolding a repaired
    task rebuilt the row without ever asking whether its reference passes.
    """
    solve = pkg / "solution" / "solve.sh"
    return solve.exists() and "# Partial:" in solve.read_text(errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_ids", nargs="+")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--replace-holdout", action="store_true",
                    help="also replace a row inside the holdout window, in place. "
                         "Changes the frozen validation set: only pass it as a "
                         "deliberate, recorded act.")
    ap.add_argument("--mix", default=None, help="default: $TRL_BASE/data/mix/live.jsonl")
    args = ap.parse_args()
    root = layout.Root.from_env()
    mix = Path(args.mix) if args.mix else root.mix.live

    lines = [l for l in mix.read_text().splitlines() if l.strip()]
    labels = [json.loads(l)["label"] for l in lines]
    existing = {json.loads(l)["label"]: json.loads(l) for l in lines}
    print(f"mix: {len(lines)} rows, holdout window = last {HOLDOUT_N} "
          f"({labels[-HOLDOUT_N]} .. {labels[-1]})")

    built: list[tuple[str, str]] = []
    for tid in args.task_ids:
        pkg = find_package(root, tid)
        if pkg is None:
            print(f"  {tid}: NO PACKAGE FOUND -- skipped")
            continue
        if partial_reference(pkg):
            print(f"  {tid}: reference solution is marked partial -- skipped")
            continue
        try:
            row = pack.to_row(str(pkg))
        except Exception as e:  # noqa: BLE001
            print(f"  {tid}: to_row failed ({type(e).__name__}: {e}) -- skipped")
            continue
        # to_row rebuilds from the package, which does not carry the fields a
        # later pass wrote onto the row: the audited daytona_* sizing and the
        # domain label. Replacing a row wholesale drops them, and the task
        # quietly falls back to the fleet defaults -- observed on tw_337683 and
        # tw_730164, whose measured 2 GiB became None on the way back in. Carry
        # anything the package cannot regenerate.
        prev = existing.get(row["label"])
        if prev is not None:
            for field in _CARRIED_FIELDS:
                if field in prev["metadata"] and field not in row["metadata"]:
                    row["metadata"][field] = prev["metadata"][field]
        # A task entering fresh is at its seed revision, as new_root.py stamps it.
        row["metadata"].setdefault("rev", 0)
        # Say where the row will LAND, not how it was built. The holdout is the
        # tail of the file and is skipped unless --replace-holdout is passed, so
        # a task that lives there is otherwise inserted before it, leaving the
        # old row in place and the id in the mix twice.
        rotation = set(labels[:-HOLDOUT_N])
        held = set(labels[-HOLDOUT_N:])
        if row["label"] in rotation:
            where = "replace-in-place"
        elif row["label"] in held:
            where = ("replace-in-holdout" if args.replace_holdout
                     else "IN HOLDOUT -- skipped, pass --replace-holdout")
        else:
            where = "insert-before-holdout"
        print(f"  {tid}: row built ({where})")
        if row["label"] in held and not args.replace_holdout:
            continue
        built.append((row["label"], json.dumps(row, ensure_ascii=False)))

    if not args.apply:
        print(f"dry run -- {len(built)} rows ready; pass --apply to write")
        return

    by_label = dict(built)
    head, tail = lines[:-HOLDOUT_N], lines[-HOLDOUT_N:]
    out, replaced = [], 0
    for l in head:
        lab = json.loads(l)["label"]
        if lab in by_label:
            out.append(by_label.pop(lab)); replaced += 1
        else:
            out.append(l)
    new_tail = []
    for l in tail:
        lab = json.loads(l)["label"]
        if args.replace_holdout and lab in by_label:
            new_tail.append(by_label.pop(lab)); replaced += 1
        else:
            new_tail.append(l)
    inserted = list(by_label.values())          # genuinely new -> before the holdout
    out.extend(inserted)
    out.extend(new_tail)                        # same length, same order

    published = layout.write_mix(mix, out)

    after = [json.loads(l)["label"] for l in mix.read_text().splitlines() if l.strip()]
    print(f"rows {len(lines)} -> {len(after)} (replaced {replaced}, inserted {len(inserted)})")
    print(f"holdout membership unchanged: "
          f"{after[-HOLDOUT_N:] == labels[-HOLDOUT_N:]}")
    if published:
        print(f"published mix v{published[0]:04d}")


if __name__ == "__main__":
    main()
