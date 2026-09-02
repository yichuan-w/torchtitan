#!/usr/bin/env python3
"""One readout for the evolution loop's health and whether tasks actually get harder.

Four sections, each from a different instrument:

  GROUPS   group-outcome mix from rollout_samples.jsonl (all-fail / all-solve /
           variance), split by policy version so the trend over training is
           visible. Variance groups are the only ones that produce gradient.
  GARBAGE  of the all-fail rollouts, how many died in <=2 turns. A hard task
           gets a real multi-turn attempt that fails; a 1-turn death is a
           degenerate generation (collapse/render bug), and counting it as
           "too hard" would poison the difficulty signal.
  EVOLVE   evolve_ondella.log actions in the last 24h + the last few lines.
  QUEUE    pending signals by direction (with the same <=2-turn garbage split),
           deferred_easier backlog, mix_live row count.

Usage: evolve_trend.py [--root /scratch/gpfs/TRIDAO/al9080/terminal-rl]
                       [--dump <run dump dir>]  (default: newest runs/* with samples)
"""

import argparse
import collections
import json
import os
import re
import time
from pathlib import Path


def group_mix(samples_path: Path) -> None:
    # group_id recycles across boots; a group *instance* ends when we see its
    # (group_id, rollout_id) pair again. Bucket instances by the max policy
    # version seen in their turns.
    open_groups: dict[int, dict] = {}
    seen_pairs: dict[int, set] = collections.defaultdict(set)
    done = []

    def close(gid):
        g = open_groups.pop(gid, None)
        if g:
            done.append(g)
        seen_pairs[gid].clear()

    with open(samples_path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("is_validation"):
                continue
            gid, rid = r.get("group_id"), r.get("rollout_id")
            if gid is None or rid is None:
                continue
            if rid in seen_pairs[gid]:
                close(gid)
            seen_pairs[gid].add(rid)
            g = open_groups.setdefault(gid, {"rewards": [], "turns": [], "pv": 0})
            g["rewards"].append(float(r.get("reward") or 0.0))
            g["turns"].append(len(r.get("turns") or []))
            for t in r.get("turns") or []:
                g["pv"] = max(g["pv"], int(t.get("max_policy_version") or 0))
    done.extend(open_groups.values())

    by_pv = collections.defaultdict(lambda: collections.Counter())
    garbage_fail, real_fail = 0, 0
    for g in done:
        rw = g["rewards"]
        if len(rw) < 2:
            continue
        solved = sum(1 for x in rw if x > 0)
        cls = ("all_fail" if solved == 0
               else "all_solve" if solved == len(rw) else "variance")
        by_pv[g["pv"]][cls] += 1
        by_pv[g["pv"]]["n"] += 1
        if cls == "all_fail":
            # a group whose median attempt died in <=2 turns is degenerate
            med_turns = sorted(g["turns"])[len(g["turns"]) // 2]
            if med_turns <= 2:
                garbage_fail += 1
            else:
                real_fail += 1

    print("GROUPS (per policy version: variance is the only gradient source)")
    for pv in sorted(by_pv):
        c = by_pv[pv]
        n = c["n"]
        print(f"  pv={pv:<3d} n={n:<4d} variance={c['variance']:<4d}"
              f" ({100 * c['variance'] / n:4.1f}%)  all_fail={c['all_fail']:<4d}"
              f" all_solve={c['all_solve']:<4d}")
    tot_fail = garbage_fail + real_fail
    if tot_fail:
        print(f"GARBAGE  all-fail groups: {tot_fail}, of which median-attempt "
              f"<=2 turns: {garbage_fail} ({100 * garbage_fail / tot_fail:.0f}%)"
              f" <- degenerate generations, NOT hard tasks")


def evolve_activity(log_path: Path) -> None:
    pat = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?(\S+) solved=(\d+)/(\d+) -> "
        r"(\w+) \(([^)]*)\)")
    cutoff = time.time() - 24 * 3600
    counts = collections.Counter()
    recent = []
    if not log_path.exists():
        print(f"EVOLVE   no log at {log_path}")
        return
    for line in open(log_path, errors="replace"):
        m = pat.match(line)
        if not m:
            continue
        ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        if ts < cutoff:
            continue
        action, status = m.group(5), m.group(6).split(",")[0]
        counts[f"{action}:{status}"] += 1
        recent.append(line.rstrip()[:150])
    print("EVOLVE   last 24h:", dict(counts) or "nothing")
    for line in recent[-5:]:
        print("  " + line)


def queue_state(root: Path) -> None:
    sig_dir = root / "evolution/signals"
    dirs = collections.Counter()
    garbage = 0
    sigs = sorted(sig_dir.glob("*.json")) if sig_dir.is_dir() else []
    for p in sigs:
        try:
            s = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        dirs[s.get("direction", "?")] += 1
        attempts = s.get("attempts") or []
        turns = sorted(len(a.get("transcript") or []) for a in attempts)
        if turns and turns[len(turns) // 2] <= 2 and s.get("direction") == "easier":
            garbage += 1
    deferred = len(list((root / "evolution/deferred_easier").glob("*"))) \
        if (root / "evolution/deferred_easier").is_dir() else 0
    mix = os.environ.get(
        "SWE_PROMPT_DATA", str(root / "data/mix/mix_live.jsonl"))
    mix_rows = sum(1 for _ in open(mix)) if os.path.exists(mix) else -1
    print(f"QUEUE    signals pending={len(sigs)} {dict(dirs)}"
          f" (easier w/ median<=2-turn transcripts: {garbage});"
          f" deferred_easier={deferred}; mix_live rows={mix_rows}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/scratch/gpfs/TRIDAO/al9080/terminal-rl")
    ap.add_argument("--dump", default=None)
    args = ap.parse_args()
    root = Path(args.root)

    dump = Path(args.dump) if args.dump else None
    if dump is None:
        cands = sorted(root.glob("runs/*/outputs/rl/rollout_samples.jsonl"),
                       key=lambda p: p.stat().st_mtime)
        if cands:
            dump = cands[-1].parents[2]
    if dump and (dump / "outputs/rl/rollout_samples.jsonl").exists():
        group_mix(dump / "outputs/rl/rollout_samples.jsonl")
    else:
        print("GROUPS   no rollout_samples.jsonl found")
    evolve_activity(root / "logs/evolve_ondella.log")
    queue_state(root)


if __name__ == "__main__":
    main()
