#!/usr/bin/env python3
"""Finalize Codex trace records left with status ``running`` after loop shutdown."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import time
from pathlib import Path


def _write_json_atomic(path: Path, value: dict) -> None:
    incoming = path.with_suffix(path.suffix + ".incoming")
    mode = stat.S_IMODE(path.stat().st_mode)
    incoming.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    incoming.chmod(mode)
    os.replace(incoming, path)


def finalize_interrupted_traces(
    root: Path,
    *,
    stopped_loop_pid: int,
    started_before_unix_ns: int | None = None,
) -> dict[str, int]:
    """Back up and mark running traces older than the optional cutoff as interrupted."""
    counts = {"marked": 0, "skipped": 0, "failed": 0}
    if not root.is_dir():
        return counts

    for trace_path in sorted(root.glob("codex-*/trace.json")):
        try:
            record = json.loads(trace_path.read_text())
            if record.get("status") != "running":
                counts["skipped"] += 1
                continue
            started = int(record.get("started_time_unix_ns") or 0)
            if started_before_unix_ns is not None and started >= started_before_unix_ns:
                counts["skipped"] += 1
                continue

            backup = trace_path.with_name("trace.pre-finalize.json")
            if not backup.exists():
                shutil.copy2(trace_path, backup)
            observed = time.time_ns()
            record.update(
                {
                    "status": "interrupted",
                    "finished_time_unix_ns": observed,
                    "finished_time_source": "restart_observation",
                    "interruption_reason": "evolve_loop_process_group_stopped",
                    "stopped_loop_pid": stopped_loop_pid,
                }
            )
            _write_json_atomic(trace_path, record)
            counts["marked"] += 1
            print(
                json.dumps(
                    {
                        "time_unix_ns": observed,
                        "outcome": "marked_interrupted",
                        "task_id": record.get("task_id"),
                        "trace_dir": str(trace_path.parent),
                    },
                    sort_keys=True,
                )
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            counts["failed"] += 1
            print(
                json.dumps(
                    {
                        "time_unix_ns": time.time_ns(),
                        "outcome": "finalize_failed",
                        "trace_file": str(trace_path),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    sort_keys=True,
                )
            )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--stopped-loop-pid", required=True, type=int)
    parser.add_argument("--started-before-unix-ns", type=int)
    args = parser.parse_args()
    counts = finalize_interrupted_traces(
        args.root,
        stopped_loop_pid=args.stopped_loop_pid,
        started_before_unix_ns=args.started_before_unix_ns,
    )
    print(json.dumps({"outcome": "finalize_summary", **counts}, sort_keys=True))
    return int(counts["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
