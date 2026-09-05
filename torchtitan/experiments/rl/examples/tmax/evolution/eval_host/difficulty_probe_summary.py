#!/usr/bin/env python3
"""Per-task pass counts of a difficulty probe run.

    difficulty_probe_summary.py <run-dir>

The eval recipe writes every validation trial to
<run-dir>/trainer/validation_traces/step-0/index.json (one record per
trial: task, state, reward, turns, finish reason) with the trace itself
beside it. This reads that index, writes <run-dir>/summary.json and prints
one line per task: passes/k and how the failures ended. 0/k is too hard for
this policy, k/k too easy; the band between is what the trainer learns from.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def _get(row: dict, *names, default=None):
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return default


def main() -> None:
    run = Path(sys.argv[1])
    steps = sorted((run / "trainer" / "validation_traces").glob("step-*"))
    if not steps:
        print(f"no validation traces under {run}", file=sys.stderr)
        sys.exit(1)
    index = json.loads((steps[-1] / "index.json").read_text())
    rows = index if isinstance(index, list) else (index.get("rollouts") or index.get("trials") or [])
    per: dict[str, dict] = {}
    for r in rows:
        tid = str(_get(r, "task", "task_id", "instance_id", default="?"))
        t = per.setdefault(tid, {"n": 0, "passed": 0, "finish": collections.Counter(), "turns": []})
        t["n"] += 1
        reward = float(_get(r, "reward", "sparse_reward", default=0) or 0)
        t["passed"] += int(reward >= 1.0)
        t["finish"][str(_get(r, "finish_reason", "state", default="?"))] += 1
        turns = _get(r, "turns", "agent_turns")
        if isinstance(turns, (int, float)):
            t["turns"].append(int(turns))
    expected = [json.loads(l)["label"] for l in open(run / "tasks.jsonl") if l.strip()]
    out = []
    for tid in expected:
        t = per.get(tid, {"n": 0, "passed": 0, "finish": collections.Counter(), "turns": []})
        band = ("no trials" if t["n"] == 0 else "too hard (0/k)" if t["passed"] == 0
                else "too easy (k/k)" if t["passed"] == t["n"] else "in band")
        out.append({"task_id": tid, "n": t["n"], "passed": t["passed"], "band": band,
                    "finish": dict(t["finish"]),
                    "turns_median": sorted(t["turns"])[len(t["turns"]) // 2] if t["turns"] else None,
                    "traces": str(steps[-1].relative_to(run))})
    (run / "summary.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"{'task':<14} {'pass':>6}  band            turns  finish")
    for r in out:
        print(f"{r['task_id']:<14} {r['passed']:>2}/{r['n']:<3}  {r['band']:<15} {str(r['turns_median']):>5}  {r['finish']}")


if __name__ == "__main__":
    main()
