#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Mark the sessions and rewrites a stopped loop left `running`.

restart_evolve.sh stops the loop's whole process group, so every codex
session alive at that moment died with it and every rewrite waiting on one
never reached its verdict. Their records still say `running`, which reads as
live. After acquiring its singleton lock, a new loop also calls this function
to recover records left by a process that exited before the restart script
could stop it. This walks tasks/*/rewrites/*/ under the root, writes
`interrupted` into records still marked `running`, and fills missing rewrite
outcomes. Account-auth links left by stopped sessions are removed without
touching their targets. Records that already finished retain their status.

    finalize_interrupted_traces.py --stopped-loop-pid <pid>     (TRL_BASE set)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402
from torchtitan.experiments.rl.examples.tmax.evolution_metrics import record_outcome  # noqa: E402


def _mark(path: Path, *, stopped_loop_pid: int | None, observed: str) -> str:
    """'marked', 'skipped' or raises."""
    record = json.loads(path.read_text())
    # SIGKILL bypasses the session's finally; never dereference shared auth.
    auth_link = path.parent / "codex" / "auth.json"
    if auth_link.is_symlink():
        auth_link.unlink()
    if record.get("status") != "running":
        return "skipped"
    record.update(
        {
            "status": "interrupted",
            "finished": observed,
            "error": (
                f"evolve loop process group stopped (pid {stopped_loop_pid})"
                if stopped_loop_pid is not None
                else "evolve loop exited before this rewrite finished"
            ),
        }
    )
    if stopped_loop_pid is not None:
        record["stopped_loop_pid"] = stopped_loop_pid
    layout.write_json_atomic(path, record)
    return "marked"


def finalize_interrupted(
    root: layout.Root, *, stopped_loop_pid: int | None = None
) -> dict[str, int]:
    counts = {"marked": 0, "skipped": 0, "failed": 0}
    observed = layout.stamp()
    outcomes_by_run: dict[str, set[str]] = {}
    for task in root.evolution.task_dirs():
        for rewrite in task.rewrite_dirs():
            records = [s.meta for s in rewrite.session_dirs()] + [rewrite.meta]
            for path in records:
                if not path.exists():
                    continue
                try:
                    outcome = _mark(
                        path, stopped_loop_pid=stopped_loop_pid, observed=observed
                    )
                except (OSError, ValueError, TypeError) as exc:
                    counts["failed"] += 1
                    print(
                        json.dumps(
                            {
                                "outcome": "finalize_failed",
                                "file": str(path),
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                            sort_keys=True,
                        )
                    )
                    continue
                counts[outcome] += 1
                if path == rewrite.meta:
                    meta = json.loads(path.read_text())
                    signal = meta.get("signal")
                    if (
                        signal
                        and not meta.get("dry")
                        and meta.get("status")
                        in {"accepted", "failed", "rejected", "kept", "interrupted"}
                    ):
                        run_name = signal.split("/", 1)[0]
                        if run_name not in outcomes_by_run:
                            outcomes_by_run[run_name] = {
                                event["rewrite"]
                                for event in layout.read_jsonl(
                                    root.evolution.run_outcomes(run_name)
                                )
                            }
                        identity = str(rewrite.path.relative_to(root.path))
                        if identity not in outcomes_by_run[run_name]:
                            record_outcome(root, rewrite, meta)
                            outcomes_by_run[run_name].add(identity)
                if outcome == "marked":
                    print(
                        json.dumps(
                            {
                                "outcome": "marked_interrupted",
                                "task": task.task_id,
                                "file": str(path.relative_to(root.path)),
                            },
                            sort_keys=True,
                        )
                    )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stopped-loop-pid", required=True, type=int)
    args = parser.parse_args()
    counts = finalize_interrupted(
        layout.Root.from_env(), stopped_loop_pid=args.stopped_loop_pid
    )
    print(json.dumps({"outcome": "finalize_summary", **counts}, sort_keys=True))
    return int(counts["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
