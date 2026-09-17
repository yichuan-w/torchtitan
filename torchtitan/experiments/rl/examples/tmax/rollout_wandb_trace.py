#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Put a run's rollouts on W&B as native traces, one span per phase.

The per-group metrics say what a phase cost on average. A trace says where one
rollout's wall clock went, in order: the sandbox boot, then the agent loop with
one span per command it ran, then grading. The commands carry their own clock,
so the **gaps between them are the time spent waiting for the generator** --
which is the thing a pass rate cannot show and the thing that decides whether a
rollout that ran out of budget was working or queueing.

Reads the records the trainer already wrote (LAYOUT.md: line 1 is the outcome,
its `exec` list is the command timeline, its `timing` block the phases), so it
runs against a finished run or eval and changes nothing in the hot path. Older
records predate `timing`; those traces carry the command spans alone.

    rollout_wandb_trace.py --run-dir <run or eval dir> [--limit 20]
        [--project titan_rl] [--entity <team>] [--new-run] [--dry]
    rollout_wandb_trace.py --run-dir <live run> --watch [--interval 300]

By default it resumes the run's own W&B run (read from `trainer/wandb/run-*`)
so the traces land beside its curves; --new-run logs them to a fresh run
instead, which is what to use when the original is someone else's.

--watch follows a run while it trains, logging the traces of records as they
appear. It always uses its own run, like the reward observer does and for the
same reason: two processes writing one W&B history lose each other's steps. It
exits once nothing new has appeared for --idle-exit seconds, so the unit that
started it does not outlive the training.

The sample is the slowest half and the fastest half by agent-loop length: a
starved rollout and a healthy one side by side is what makes the gaps legible.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

RUN_DIR_RE = re.compile(r"run-\d{8}_\d{6}-(?P<id>\w+)$")


def _records(run_dir: str) -> list[tuple[str, dict]]:
    """(path, line-1 object) for every rollout record under the run."""
    out = []
    for sub in ("rollouts", "validation_rollouts"):
        for path in glob.glob(os.path.join(run_dir, sub, "*", "*.jsonl")):
            try:
                with open(path) as fh:
                    out.append((path, json.loads(fh.readline())))
            except Exception:  # noqa: BLE001 -- a half-written record is skipped
                continue
    return out


def _loop_span(rec: dict) -> tuple[float, float] | None:
    """(first command start, last command end) in epoch seconds."""
    execs = rec.get("exec") or []
    if not execs:
        return None
    return execs[0]["t"], execs[-1]["t"] + execs[-1].get("secs", 0.0)


def _wandb_run_id(run_dir: str) -> str | None:
    for path in sorted(glob.glob(os.path.join(run_dir, "trainer", "wandb", "run-*"))):
        match = RUN_DIR_RE.search(path)
        if match:
            return match.group("id")
    return None


def _pick(records: list[tuple[str, dict]], limit: int) -> list[tuple[str, dict]]:
    with_span = [(p, r) for p, r in records if _loop_span(r)]
    with_span.sort(key=lambda pr: _loop_span(pr[1])[1] - _loop_span(pr[1])[0])
    if len(with_span) <= limit:
        return with_span
    half = limit // 2
    return with_span[:half] + with_span[len(with_span) - (limit - half) :]


def build_trace(rec: dict, path: str):
    from wandb.sdk.data_types.trace_tree import Trace

    start, end = _loop_span(rec)
    timing = rec.get("timing") or {}
    ms = lambda t: int(t * 1000)  # noqa: E731 -- Trace wants epoch milliseconds

    # The boot happened before the first command; the record keeps its length,
    # not its start, so place it immediately before the loop.
    boot = timing.get("boot_secs")
    grade = timing.get("grade_secs")
    root_start = start - boot if boot else start
    root_end = end + grade if grade else end

    name = f"{rec.get('task')} g{rec.get('group')}-r{rec.get('rollout')}"
    root = Trace(
        name=name,
        kind="chain",
        start_time_ms=ms(root_start),
        end_time_ms=ms(root_end),
        status_code="success" if rec.get("reward") == 1.0 else "error",
        status_message=str(rec.get("finish_reason") or ""),
        metadata={
            "task": rec.get("task"),
            "rev": rec.get("rev"),
            "reward": rec.get("reward"),
            "finish_reason": rec.get("finish_reason"),
            "submitted": rec.get("submitted"),
            "turns": rec.get("turns"),
            "secs": rec.get("secs"),
            "budget_sec": rec.get("budget_sec"),
            "infra_failed": rec.get("infra_failed"),
            "record": path,
            **{k: v for k, v in timing.items()},
        },
    )
    if boot:
        root.add_child(
            Trace(
                name="boot",
                kind="tool",
                start_time_ms=ms(root_start),
                end_time_ms=ms(start),
            )
        )
    loop = Trace(
        name="agent loop",
        kind="agent",
        start_time_ms=ms(start),
        end_time_ms=ms(end),
        metadata={
            "commands": len(rec.get("exec") or []),
            # What the loop did NOT spend on commands is what it spent waiting.
            "command_secs": round(
                sum(e.get("secs", 0.0) for e in rec.get("exec") or []), 1
            ),
            "loop_secs": round(end - start, 1),
        },
    )
    for entry in rec.get("exec") or []:
        cmd = str(entry.get("cmd") or "")
        loop.add_child(
            Trace(
                name=cmd.split("\n", 1)[0][:60] or "exec",
                kind="tool",
                start_time_ms=ms(entry["t"]),
                end_time_ms=ms(entry["t"] + entry.get("secs", 0.0)),
                status_code="success" if entry.get("exit") == 0 else "error",
                inputs={"cmd": cmd[:2000]},
            )
        )
    root.add_child(loop)
    if grade:
        root.add_child(
            Trace(
                name="grade",
                kind="tool",
                start_time_ms=ms(end),
                end_time_ms=ms(root_end),
                status_code="success" if rec.get("reward") == 1.0 else "error",
            )
        )
    return root


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--run-dir", required=True, help="a run or eval directory")
    ap.add_argument("--limit", type=int, default=20, help="how many to trace")
    ap.add_argument("--project", default=os.environ.get("WANDB_PROJECT", "titan_rl"))
    ap.add_argument("--entity", default=os.environ.get("WANDB_ENTITY"))
    ap.add_argument(
        "--new-run",
        action="store_true",
        help="log to a fresh run instead of resuming the run's own",
    )
    ap.add_argument("--dry", action="store_true", help="print the sample, log nothing")
    ap.add_argument(
        "--watch", action="store_true", help="follow a live run, logging as it goes"
    )
    ap.add_argument("--interval", type=int, default=300, help="--watch poll seconds")
    ap.add_argument(
        "--idle-exit",
        type=int,
        default=1800,
        help="--watch gives up after this long with no new record",
    )
    a = ap.parse_args()

    if a.watch:
        return _watch(a)

    run_dir = a.run_dir.rstrip("/")
    records = _records(run_dir)
    if not records:
        sys.exit(f"no rollout records under {run_dir}")
    picked = _pick(records, a.limit)
    print(f"{len(records)} records, tracing {len(picked)}")
    for path, rec in picked:
        span = _loop_span(rec)
        print(
            f"  {rec.get('task')} g{rec.get('group')}-r{rec.get('rollout')} "
            f"loop={span[1] - span[0]:7.0f}s commands={len(rec.get('exec') or []):4d} "
            f"reward={rec.get('reward')} {rec.get('finish_reason')}"
        )
    if a.dry:
        return 0

    import wandb

    resume_id = None if a.new_run else _wandb_run_id(run_dir)
    if resume_id:
        run = wandb.init(
            project=a.project,
            entity=a.entity,
            id=resume_id,
            resume="must",
            settings=wandb.Settings(silent=True),
        )
        print(f"resumed {run.url}")
    else:
        run = wandb.init(
            project=a.project,
            entity=a.entity,
            name=f"trace-{os.path.basename(run_dir)}",
            settings=wandb.Settings(silent=True),
        )
        print(f"new run {run.url}")
    for path, rec in picked:
        build_trace(rec, path).log(name="rollout_trace")
    run.finish()
    print(f"logged {len(picked)} traces")
    return 0


def _watch(a) -> int:
    """Log traces for records as a live run writes them, then exit when it stops."""
    import wandb

    run_dir = a.run_dir.rstrip("/")
    run = wandb.init(
        project=a.project,
        entity=a.entity,
        name=f"trace-{os.path.basename(run_dir)}",
        settings=wandb.Settings(silent=True),
    )
    print(f"watching {run_dir} -> {run.url}", flush=True)
    seen: set[str] = set()
    idle = 0.0
    while idle < a.idle_exit:
        fresh = [(p, r) for p, r in _records(run_dir) if p not in seen]
        seen.update(p for p, _ in fresh)
        picked = _pick(fresh, a.limit)
        for path, rec in picked:
            build_trace(rec, path).log(name="rollout_trace")
        if fresh:
            idle = 0.0
            print(
                f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] "
                f"{len(fresh)} new records, logged {len(picked)} traces",
                flush=True,
            )
        else:
            idle += a.interval
        time.sleep(a.interval)
    print(f"no new record for {a.idle_exit}s; done", flush=True)
    run.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
