#!/usr/bin/env python3
"""One readout for the evolution loop's health and whether tasks actually get harder.

Four sections, each from a different instrument, all under one root:

  GROUPS   group-outcome mix from the run's rollout records (all-fail /
           all-solve / variance), per six-hour window so the trend over
           training is visible, and per task revision so seeds (rev 0) and
           rewrites (rev >= 1) sit side by side. Variance groups are the only
           ones that produce gradient.
  GARBAGE  of the all-fail groups, how many died in <=2 turns. A hard task
           gets a real multi-turn attempt that fails; a 1-turn death is a
           degenerate generation (collapse/render bug), and counting it as
           "too hard" would poison the difficulty signal.
  EVOLVE   the ledger's last 24h by direction and outcome, the rewrites that
           finished in that window by job and status, and the loop.log tail.
  QUEUE    pending signals by direction (with the same <=2-turn garbage
           split), the deferred backlog, and the mix as status.json sees it.

Usage: evolve_trend.py [--root $TRL_BASE] [--run <name>]   (default run: runs/latest)
"""

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import layout  # noqa: E402
import rollout_record  # noqa: E402

WINDOW_SEC = 6 * 3600


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _outcome(headers: list[dict]) -> str:
    solved = sum(1 for h in headers if float(h.get("reward") or 0.0) > 0)
    return ("all_fail" if solved == 0
            else "all_solve" if solved == len(headers) else "variance")


def _print_mix(label: str, c: collections.Counter) -> None:
    n = c["n"]
    print(f"  {label:<9} n={n:<4d} variance={c['variance']:<4d}"
          f" ({100 * c['variance'] / n:4.1f}%)  all_fail={c['all_fail']:<4d}"
          f" all_solve={c['all_solve']:<4d}")


def group_mix(run: layout.Run) -> None:
    # One run is one process lifetime, so a group id names one group here;
    # the recycling across boots that the old dump needed untangling is gone.
    groups: dict[int, list[dict]] = collections.defaultdict(list)
    for p in run.rollouts.glob("*/g*-r*.jsonl"):
        try:
            h = rollout_record.read_header(p)
        except (OSError, ValueError):
            continue
        groups[int(h["group"])].append(h)

    by_window: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    by_rev: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    garbage_fail = real_fail = 0
    starts = [layout.parse_stamp(h["started"]) for hs in groups.values() for h in hs if h.get("started")]
    t0 = min(starts) if starts else 0.0
    for hs in groups.values():
        if len(hs) < 2:
            continue
        cls = _outcome(hs)
        began = min(layout.parse_stamp(h["started"]) for h in hs if h.get("started"))
        window = int((began - t0) // WINDOW_SEC)
        rev = "rev0" if all(int(h.get("rev") or 0) == 0 for h in hs) else "rev>=1"
        for bucket in (by_window[window], by_rev[rev]):
            bucket[cls] += 1
            bucket["n"] += 1
        if cls == "all_fail":
            # a group whose median attempt died in <=2 turns is degenerate
            turns = sorted(int(h.get("turns") or 0) for h in hs)
            if turns[len(turns) // 2] <= 2:
                garbage_fail += 1
            else:
                real_fail += 1

    print(f"GROUPS   {run.name}: {len(groups)} groups (variance is the only gradient source)")
    for w in sorted(by_window):
        _print_mix(f"{w * 6:>3d}-{(w + 1) * 6}h", by_window[w])
    for rev in ("rev0", "rev>=1"):
        if rev in by_rev:
            _print_mix(rev, by_rev[rev])
    tot_fail = garbage_fail + real_fail
    if tot_fail:
        print(f"GARBAGE  all-fail groups: {tot_fail}, of which median-attempt "
              f"<=2 turns: {garbage_fail} ({100 * garbage_fail / tot_fail:.0f}%)"
              f" <- degenerate generations, NOT hard tasks")


def evolve_activity(evo: layout.Evolution, now: float) -> None:
    cutoff = now - 24 * 3600
    signals = collections.Counter()
    for e in layout.read_jsonl(evo.ledger):
        if layout.parse_stamp(e["stamp"]) >= cutoff:
            signals[f"{e.get('direction')}:{e.get('outcome')}"] += 1
    rewrites = collections.Counter()
    for task in evo.task_dirs():
        for rw in task.rewrite_dirs():
            meta = _json(rw.meta)
            finished = meta.get("finished")
            if finished and layout.parse_stamp(finished) >= cutoff:
                rewrites[f"{meta.get('job')}:{meta.get('status')}"] += 1
    print(f"EVOLVE   last 24h signals: {dict(signals) or 'nothing'}"
          f"  rewrites finished: {dict(rewrites) or 'nothing'}")
    if not evo.loop_log.exists():
        print(f"         no log at {evo.loop_log}")
        return
    with open(evo.loop_log, errors="replace") as f:
        for line in f.readlines()[-5:]:
            print("  " + line.rstrip()[:150])


def queue_state(root: layout.Root) -> None:
    evo = root.evolution
    ledger = layout.read_jsonl(evo.ledger)
    latest_outcome = {e["signal"]: e.get("outcome") for e in ledger}
    deferred = sum(1 for o in latest_outcome.values() if o == "deferred")
    dirs = collections.Counter()
    pending = garbage = 0
    for run in root.run_dirs():
        for p in run.signal_files():
            if f"{run.name}/{p.stem}" in latest_outcome:
                continue
            s = _json(p)
            pending += 1
            dirs[s.get("direction", "?")] += 1
            turns = sorted(int(rollout_record.read_header(run.path / a).get("turns") or 0)
                           for a in s.get("attempts") or [] if (run.path / a).exists())
            if turns and turns[len(turns) // 2] <= 2 and s.get("direction") == "easier":
                garbage += 1
    status = _json(evo.status)
    live = root.mix.live_version()
    rows = _json(layout.MixDir.manifest_of(live[1])).get("rows") if live else None
    print(f"QUEUE    signals pending={pending} {dict(dirs)}"
          f" (easier w/ median<=2-turn attempts: {garbage}); deferred={deferred};"
          f" mix v{status.get('mix_version', live[0] if live else '?')} rows={rows};"
          f" rewrites_running={status.get('rewrites_running', '?')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("TRL_BASE"),
                    help="experiment root (default: $TRL_BASE)")
    ap.add_argument("--run", help="run name under runs/ (default: runs/latest)")
    args = ap.parse_args()
    if not args.root:
        ap.error("--root or TRL_BASE names the experiment root")
    root = layout.Root(Path(args.root))

    run = None
    if args.run:
        run = root.run(args.run)
    elif root.latest.exists():
        run = layout.Run(root.latest.resolve())
    if run is not None and run.rollouts.exists():
        group_mix(run)
    else:
        print(f"GROUPS   no rollouts under {run.path if run else root.runs}")
    evolve_activity(root.evolution, time.time())
    queue_state(root)


if __name__ == "__main__":
    main()
