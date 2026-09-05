#!/usr/bin/env python3
"""Mark the sessions and rewrites a stopped loop left `running`.

restart_evolve.sh stops the loop's whole process group, so every codex
session alive at that moment died with it and every rewrite waiting on one
never reached its verdict. Their records still say `running`, which reads as
live. This walks tasks/*/rewrites/*/ under the root, and writes `interrupted`
into every session.json and rewrite.json that says `running`, with when it
was observed and which loop pid was stopped. Nothing else is touched: a
record that already finished says what it says.

    finalize_interrupted_traces.py --stopped-loop-pid <pid>     (TRL_BASE set)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402


def _mark(path: Path, *, stopped_loop_pid: int, observed: str) -> str:
    """'marked', 'skipped' or raises."""
    record = json.loads(path.read_text())
    if record.get("status") != "running":
        return "skipped"
    record.update({
        "status": "interrupted",
        "finished": observed,
        "error": f"evolve loop process group stopped (pid {stopped_loop_pid})",
        "stopped_loop_pid": stopped_loop_pid,
    })
    layout.write_json_atomic(path, record)
    return "marked"


def finalize_interrupted(root: layout.Root, *, stopped_loop_pid: int) -> dict[str, int]:
    counts = {"marked": 0, "skipped": 0, "failed": 0}
    observed = layout.stamp()
    for task in root.evolution.task_dirs():
        for rewrite in task.rewrite_dirs():
            records = [s.meta for s in rewrite.session_dirs()] + [rewrite.meta]
            for path in records:
                if not path.exists():
                    continue
                try:
                    outcome = _mark(path, stopped_loop_pid=stopped_loop_pid, observed=observed)
                except (OSError, ValueError, TypeError) as exc:
                    counts["failed"] += 1
                    print(json.dumps({"outcome": "finalize_failed", "file": str(path),
                                      "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
                    continue
                counts[outcome] += 1
                if outcome == "marked":
                    print(json.dumps({"outcome": "marked_interrupted", "task": task.task_id,
                                      "file": str(path.relative_to(root.path))}, sort_keys=True))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stopped-loop-pid", required=True, type=int)
    args = parser.parse_args()
    counts = finalize_interrupted(layout.Root.from_env(), stopped_loop_pid=args.stopped_loop_pid)
    print(json.dumps({"outcome": "finalize_summary", **counts}, sort_keys=True))
    return int(counts["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
