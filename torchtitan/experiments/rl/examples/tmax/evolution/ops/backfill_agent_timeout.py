#!/usr/bin/env python3
"""Backfill or strip ``metadata.agent_timeout_sec`` on rows the mix holds.

POST-MORTEM (2026-08-29): the backfill direction is retired for training rows.
Declared budgets are sized for oracle solvers; backfilled into rotation rows
they killed 75-85% of rollouts for the 9B policy, and --strip reverted them the
same day. The standing policy: training rows carry NO agent_timeout_sec -- the
launcher's flat SWE_TIME_BUDGET_SEC=2400 governs -- and prepare_rts_data emits
a task's declared ``[agent] timeout_sec`` only under SWE_EMIT_AGENT_TIMEOUT=1,
an eval-only opt-in. Use --strip to remove the key from rotation rows; do not
re-run the backfill against the training mix.

Two things this script is careful about, because the mix is read live by a
running trainer (SWE_DATA_HOT_RELOAD=1):

  * The holdout window -- the last --holdout-n rows -- is never touched. It is
    the anchor every before/after eval comparison is measured against, so it has
    to stay byte-identical, and the script verifies that it did.
  * An unchanged row is reproduced byte for byte. The mix was written by several
    tools and 82 of its rows use \\u escapes where the rest use literal UTF-8, so
    the style is detected per row and reused. A diff of an applied run shows one
    inserted field per changed row and nothing else.

Dry run by default; --apply backs the mix up first.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import time
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("needs python3.11+ for tomllib (della: use `python3.11`)")
import tomllib

ROOT = Path(os.environ.get("TRL_ROOT", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))
HOLDOUT_N = int(os.environ.get("TMAX_HOLDOUT_N", "64"))

# Same pools refold_repaired.find_package walks, plus the evolution output the
# loop rewrites in place. All three agree wherever they overlap (518 of the 695
# tw tasks exist in both tw-extract and retuned, with identical budgets), so the
# order only decides which one answers, never what the answer is.
_POOLS = ("data/tw-extract/tasks", "data/swe-extract/tasks", "evolution/retuned")


def declared_timeout(task_id: str) -> float | None:
    """The task's own agent budget, or None if it declares none."""
    for pool in _POOLS:
        toml = ROOT / pool / task_id / "task.toml"
        if not toml.exists():
            continue
        try:
            with open(toml, "rb") as f:
                doc = tomllib.load(f)
        except Exception as e:  # noqa: BLE001 -- a malformed toml is not fatal here
            print(f"  {task_id}: unreadable {toml} ({type(e).__name__}: {e})")
            continue
        ts = (doc.get("agent") or {}).get("timeout_sec")
        if isinstance(ts, (int, float)) and ts > 0:
            return float(ts)
    return None


def _ascii_style(row: dict, original: str) -> bool | None:
    """Which ensure_ascii setting reproduces this line, or None if neither does."""
    for ea in (False, True):
        if json.dumps(row, ensure_ascii=ea) == original:
            return ea
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix", default=str(ROOT / "data/mix/mix_live.jsonl"))
    ap.add_argument("--holdout-n", type=int, default=HOLDOUT_N)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--strip", action="store_true", help=(
        "REMOVE metadata.agent_timeout_sec from rotation rows instead of adding "
        "it. Post-mortem of 08-29: backfilled budgets (mostly 600-1800s, floored "
        "to 900) were sized for oracle solvers, not a 9B policy at ~7 tok/s -- "
        "75-85%% of rollouts started dying at the budget with few or zero turns "
        "(healthy boot before the backfill: 7108 completed / 30 errors; every "
        "boot after: 3-25%% completion). The rollouter's own design note says "
        "training rows keep the launcher's SWE_TIME_BUDGET_SEC; declared "
        "budgets are for the TB-2.0 eval set, which prepare_tb2_data handles."))
    args = ap.parse_args()

    mix = Path(args.mix)
    lines = [l for l in mix.read_text().splitlines() if l.strip()]
    n_hold = args.holdout_n
    rotation, holdout = lines[:-n_hold], lines[-n_hold:]
    print(f"mix: {len(lines)} rows = {len(rotation)} in rotation + {n_hold} holdout")

    out: list[str] = []
    dist: collections.Counter = collections.Counter()
    changed = already = undeclared = restyled = 0
    if args.strip:
        for line in rotation:
            row = json.loads(line)
            tid = row["metadata"]["instance_id"]
            if row["metadata"].get("agent_timeout_sec") is None:
                already += 1
                out.append(line)
                continue
            ea = _ascii_style(row, line)
            if ea is None:
                restyled += 1
                print(f"  {tid}: line style unrecognised -- left unchanged")
                out.append(line)
                continue
            del row["metadata"]["agent_timeout_sec"]
            out.append(json.dumps(row, ensure_ascii=ea))
            changed += 1
        print(f"  strip: {changed} rows lose agent_timeout_sec, "
              f"{already} had none, {restyled} unrecognised")
        assert holdout == lines[-n_hold:], "holdout window moved"
        if not args.apply:
            print(f"dry run -- pass --apply to write ({changed} rows would change)")
            return
        bak = f"{mix}.bak-strip-timeout-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(mix, bak)
        mix.write_text("\n".join(out + holdout) + "\n")
        print(f"wrote {mix} ({changed} rows changed); backup {bak}")
        check = [l for l in mix.read_text().splitlines() if l.strip()]
        assert len(check) == len(lines), "row count moved"
        assert check[-n_hold:] == holdout, "holdout no longer byte-identical"
        assert not any(json.loads(l)["metadata"].get("agent_timeout_sec")
                       for l in check[:-n_hold]), "a rotation row kept a budget"
        print(f"verified: {len(check)} rows, holdout byte-identical, budgets gone")
        return
    for line in rotation:
        row = json.loads(line)
        tid = row["metadata"]["instance_id"]
        if row["metadata"].get("agent_timeout_sec") is not None:
            already += 1
            dist[row["metadata"]["agent_timeout_sec"]] += 1
            out.append(line)
            continue
        ts = declared_timeout(tid)
        if ts is None:
            undeclared += 1
            dist[None] += 1
            out.append(line)
            continue
        ea = _ascii_style(row, line)
        if ea is None:
            # Not written by json.dumps in either style -- reproducing it is not
            # possible, so leave it and say so rather than silently reformat.
            restyled += 1
            print(f"  {tid}: line style unrecognised -- left unchanged")
            out.append(line)
            continue
        row["metadata"]["agent_timeout_sec"] = ts
        out.append(json.dumps(row, ensure_ascii=ea))
        dist[ts] += 1
        changed += 1

    def budget(counts: collections.Counter, flat: float, floor: float) -> float:
        return sum((flat if k is None else max(k, floor)) * n for k, n in counts.items())

    print(f"  {changed} rows gain a budget, {already} already had one, "
          f"{undeclared} declare none, {restyled} unrecognised")
    print("  declared distribution: "
          + ", ".join(f"{k}s x{v}" for k, v in sorted(dist.items(), key=lambda kv: -kv[1])))
    flat = float(os.environ.get("SWE_TIME_BUDGET_SEC", "2400"))
    before = flat * len(rotation)
    print(f"  pool budget now (flat {flat:.0f}s):        {before/3600:8.1f} task-hours")
    for floor in (900, 1200, 7200):
        after = budget(dist, flat, floor)
        print(f"  pool budget at floor {floor:4d}s: {after/3600:8.1f} task-hours "
              f"({before/after:.2f}x less)")

    assert holdout == lines[-n_hold:], "holdout window moved"
    if not args.apply:
        print(f"dry run -- pass --apply to write ({changed} rows would change)")
        return

    bak = f"{mix}.bak-agent-timeout-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(mix, bak)
    mix.write_text("\n".join(out + holdout) + "\n")
    print(f"wrote {mix} ({changed} rows changed); backup {bak}")

    check = [l for l in mix.read_text().splitlines() if l.strip()]
    assert len(check) == len(lines), f"row count moved {len(lines)} -> {len(check)}"
    assert check[-n_hold:] == holdout, "holdout window is no longer byte-identical"
    print(f"verified: {len(check)} rows, holdout byte-identical")


if __name__ == "__main__":
    main()
