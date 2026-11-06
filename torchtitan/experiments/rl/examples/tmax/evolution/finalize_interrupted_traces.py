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
could stop it. This walks the tasks/*/rewrites/*/ the ledger has not
closed as handled (a closed one is final by construction), writes
`interrupted` into records still marked `running`, and fills missing rewrite
outcomes. Account-auth links left by stopped sessions are removed without
touching their targets. Records that already finished retain their status.

    finalize_interrupted_traces.py --stopped-loop-pid <pid>     (TRL_BASE set)
    finalize_interrupted_traces.py [--except-task <task> ...]  (TRL_BASE set)

--task / --except-task scope the walk to some tasks: when each task has a loop
process of its own (evolve_ondella.py --task), a task whose process is still
alive must not be touched, its auth links included.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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


# Each listdir or read on the durable FUSE mount is a ~0.3-1 s round trip;
# calls in flight overlap.
SCAN_WORKERS = 16


def closed_rewrites(root: layout.Root) -> set[tuple[str, str]]:
    """(task dir, rewrite dir) of every rewrite the ledger closed as handled.

    _close writes the terminal rewrite.json and the outcome event before the
    ``handled`` line, and each session's record is final (evolve_codex's
    finally) before the rewrite has a verdict, so nothing under a closed
    rewrite can still read ``running``. A line that does not parse closes
    nothing, which only widens the scan.
    """
    closed: set[tuple[str, str]] = set()
    try:
        text = root.evolution.ledger.read_text(encoding="utf-8")
    except FileNotFoundError:
        return closed
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("outcome") != "handled":
            continue
        parts = Path(str(entry.get("rewrite") or "")).parts
        if len(parts) == 4 and parts[0] == "tasks" and parts[2] == "rewrites":
            closed.add((parts[1], parts[3]))
    return closed


def _subdirs(path: Path) -> list[Path]:
    # scandir's d_type spares the per-entry stat Path.is_dir() costs.
    try:
        with os.scandir(path) as entries:
            return sorted(Path(e.path) for e in entries if e.is_dir())
    except FileNotFoundError:
        return []


def _in_scope(name: str, tasks, except_tasks) -> bool:
    if tasks is not None and name not in tasks:
        return False
    return not (except_tasks and name in except_tasks)


def open_rewrites(
    root: layout.Root,
    *,
    include: tuple[layout.RewriteDir, ...] = (),
    tasks=None,
    except_tasks=(),
) -> list[tuple[str, layout.RewriteDir]]:
    """(task id, rewrite) for every rewrite the ledger has not closed, plus
    ``include``. Lists every task's rewrites/, in parallel. ``tasks`` (when
    not None) and ``except_tasks`` scope it by task directory name."""
    closed = closed_rewrites(root)
    tasks = [
        t
        for t in _subdirs(root.evolution.tasks)
        if _in_scope(t.name, tasks, except_tasks)
    ]
    with ThreadPoolExecutor(SCAN_WORKERS, thread_name_prefix="finalize-scan") as pool:
        listed = list(pool.map(lambda t: _subdirs(t / "rewrites"), tasks))
    found = {
        rw: task.name
        for task, rewrites in zip(tasks, listed)
        for rw in rewrites
        if (task.name, rw.name) not in closed
    }
    for rw in include:
        found.setdefault(rw.path, rw.path.parent.parent.name)
    return [(found[p], layout.RewriteDir(p)) for p in sorted(found)]


def finalize_interrupted(
    root: layout.Root,
    *,
    stopped_loop_pid: int | None = None,
    include: tuple[layout.RewriteDir, ...] | list[layout.RewriteDir] = (),
    tasks=None,
    except_tasks=(),
) -> dict[str, int]:
    """Mark what a stopped loop left ``running`` under every open rewrite.

    Rewrites the ledger closed are not read (closed_rewrites), so the cost
    follows what was in flight rather than the whole history. ``include`` is
    examined regardless of the ledger.
    """
    counts = {"marked": 0, "skipped": 0, "failed": 0}
    observed = layout.stamp()
    outcomes_by_run: dict[str, set[str]] = {}
    lock = threading.Lock()

    def emit(event: dict) -> None:
        with lock:
            print(json.dumps(event, sort_keys=True), flush=True)

    def backfill_outcome(rewrite: layout.RewriteDir) -> None:
        meta = json.loads(rewrite.meta.read_text())
        signal = meta.get("signal")
        if not (
            signal
            and not meta.get("dry")
            and meta.get("status")
            in {"accepted", "failed", "rejected", "kept", "interrupted"}
        ):
            return
        run_name = signal.split("/", 1)[0]
        identity = str(rewrite.path.relative_to(root.path))
        # Read and append under the lock: two threads never both record one.
        with lock:
            if run_name not in outcomes_by_run:
                outcomes_by_run[run_name] = {
                    event["rewrite"]
                    for event in layout.read_jsonl(
                        root.evolution.run_outcomes(run_name)
                    )
                }
            if identity not in outcomes_by_run[run_name]:
                record_outcome(root, rewrite, meta)
                outcomes_by_run[run_name].add(identity)

    def one(item: tuple[str, layout.RewriteDir]) -> None:
        task_id, rewrite = item
        records = [s.meta for s in rewrite.session_dirs()] + [rewrite.meta]
        for path in records:
            if not path.exists():
                continue
            try:
                outcome = _mark(
                    path, stopped_loop_pid=stopped_loop_pid, observed=observed
                )
            except (OSError, ValueError, TypeError) as exc:
                with lock:
                    counts["failed"] += 1
                emit(
                    {
                        "outcome": "finalize_failed",
                        "file": str(path),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            with lock:
                counts[outcome] += 1
            if path == rewrite.meta:
                backfill_outcome(rewrite)
            if outcome == "marked":
                emit(
                    {
                        "outcome": "marked_interrupted",
                        "task": task_id,
                        "file": str(path.relative_to(root.path)),
                    }
                )

    candidates = open_rewrites(
        root, include=tuple(include), tasks=tasks, except_tasks=except_tasks
    )
    with ThreadPoolExecutor(SCAN_WORKERS, thread_name_prefix="finalize") as pool:
        # list() re-raises the first failure, as the serial loop did.
        list(pool.map(one, candidates))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stopped-loop-pid", type=int)
    parser.add_argument("--task", action="append", help="only this task (repeatable)")
    parser.add_argument(
        "--except-task", action="append", default=[], help="not this task (repeatable)"
    )
    args = parser.parse_args()
    counts = finalize_interrupted(
        layout.Root.from_env(),
        stopped_loop_pid=args.stopped_loop_pid,
        tasks=set(args.task) if args.task is not None else None,
        except_tasks=set(args.except_task),
    )
    print(json.dumps({"outcome": "finalize_summary", **counts}, sort_keys=True))
    return int(counts["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
