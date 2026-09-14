#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Did the rewrite actually move the task's difficulty? Re-solve and compare.

The loop folds an accepted rewrite back and moves on; nothing measures whether
the task's difficulty actually moved toward target. This closes that loop
directly: take each task's latest accepted rewrite, re-solve the revision it
produced pass@k, and compare to the before-rate recorded by the signal the
rewrite answered.

  before (from the signal):  easier direction was 0/16, harder was 16/16
  after  (measured here):    r<result_rev>'s pass@k now

  moved_toward_target (legacy): easier escaped zero; harder escaped full.
  This endpoint metric can be true at the opposite endpoint or on a graded
  subset. It does not mean the task entered the target interval.

  outcome: all_solved / partial_solved / none_solved only when every planned
  attempt is graded; otherwise incomplete. The target interval is strictly
  between zero and full in both directions. Neither metric establishes task
  validity or stable improvement without independent evidence.

Runs at MAX concurrency (auto_delete=15min keeps the account clean regardless).
Results and log land under the root's logs/measure_difficulty--<stamp>/.

  measure_difficulty.py --limit 30 --concurrency 200
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout  # noqa: E402
import solve_daytona as sd  # noqa: E402

log = logging.getLogger("measure_difficulty")


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def accepted_rewrites(root: layout.Root, limit: int) -> list[dict]:
    """Each task's latest accepted rewrite, with the before-rate from the
    signal it answered; oldest first, the newest ``limit`` kept.

    An accepted rewrite's package is ``r<result_rev>/`` under the task, so
    there is always an evolved artifact to re-solve; the signal it names sits
    in the run that emitted it."""
    latest: dict[str, tuple[layout.TaskDir, layout.RewriteDir, dict]] = {}
    for task in root.evolution.task_dirs():
        for rw in task.rewrite_dirs():  # named by stamp, so sorted is oldest first
            meta = _json(rw.meta)
            if meta.get("status") == "accepted" and meta.get("result_rev") is not None:
                latest[task.task_id] = (task, rw, meta)
    rows = []
    for tid, (task, rw, meta) in latest.items():
        run_name, name = meta["signal"].split("/", 1)
        signal = _json(root.run(run_name).signals / f"{name}.json")
        rows.append(
            {
                "task": tid,
                "rewrite": str(rw.path.relative_to(root.evolution.path)),
                "job": meta.get("job"),
                "input_rev": meta.get("input_rev"),
                "rev": meta["result_rev"],
                "package": str(task.rev(meta["result_rev"])),
                "signal": meta["signal"],
                "before_solved": signal.get("solved"),
                "before_total": signal.get("total"),
                "finished": meta.get("finished"),
            }
        )
    rows.sort(key=lambda r: r["finished"] or "")
    return rows[-limit:] if limit else rows


async def run(root: layout.Root, args: argparse.Namespace, out_dir: Path) -> None:
    rows = accepted_rewrites(root, args.limit)
    print(f"tasks with an accepted rewrite: {len(rows)}")
    for r in rows:
        log.info(
            "start %s %s -> r%s before=%s/%s",
            r["task"],
            r["rewrite"],
            r["rev"],
            r["before_solved"],
            r["before_total"],
        )
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(
        *[
            sd.solve_task(
                r["task"], args.attempts, args.max_turns, sem, src=Path(r["package"])
            )
            for r in rows
        ]
    )
    out = out_dir / "results.jsonl"
    moved = same = complete = in_target = 0
    with open(out, "w") as f:
        for b, res in zip(rows, results):
            after, graded = res.get("solved"), res.get("graded")
            # Keep the historical endpoint metric for graded results.
            ok = None
            if graded and after is not None:
                if b["job"] == "easier":
                    ok = after > 0
                elif b["job"] == "harder":
                    ok = after < graded
            outcome = "incomplete"
            target = None
            if graded == args.attempts and graded > 0 and after is not None:
                target = 0 < after < graded
                outcome = (
                    "partial_solved"
                    if target
                    else "all_solved"
                    if after == graded
                    else "none_solved"
                )
                complete += 1
                in_target += target
            rec = {
                **b,
                "after_solved": after,
                "after_graded": graded,
                "after_pass_at_k": res.get("pass_at_k"),
                "moved_toward_target": ok,
                "outcome": outcome,
                "in_target_interval": target,
                "planned_attempts": args.attempts,
                "instruction": res.get("instruction"),
                "attempts": res.get("attempts"),
                "status": res.get("status"),
                "why": res.get("why"),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            if ok is True:
                moved += 1
            elif ok is False:
                same += 1
            log.info(
                "%s r%s [%s] before=%s/%s after=%s/%s -> legacy_endpoint_moved=%s outcome=%s",
                b["task"],
                b["rev"],
                b["job"],
                b["before_solved"],
                b["before_total"],
                after,
                graded,
                ok,
                outcome,
            )
    n = moved + same
    print("\n=== difficulty movement ===")
    print(f"legacy endpoint movement (graded subsets included): {n}")
    if n:
        print(f"escaped original endpoint: {moved}/{n} ({moved / n:.0%})")
        print(f"did not escape original endpoint: {same}/{n}")
    print(
        f"complete measurements: {complete}/{len(rows)}; incomplete: {len(rows) - complete}"
    )
    print(f"in target interval (partial_solved): {in_target}/{complete}")
    print(
        "Endpoint movement is not target-interval success; these measurements alone "
        "do not establish validity or stable improvement."
    )
    print(f"records: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--root",
        default=os.environ.get("TRL_BASE"),
        help="experiment root (default: $TRL_BASE)",
    )
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--concurrency", type=int, default=200)
    args = ap.parse_args()
    if not args.root:
        ap.error("--root or TRL_BASE names the experiment root")
    root = layout.Root(Path(args.root))
    # One invocation, one directory: the log and the records it explains.
    out_dir = root.logs / f"measure_difficulty--{layout.stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(out_dir / "measure_difficulty.log"),
            logging.StreamHandler(),
        ],
    )
    log.info(
        "root=%s limit=%d attempts=%d max_turns=%d concurrency=%d out=%s",
        root.path,
        args.limit,
        args.attempts,
        args.max_turns,
        args.concurrency,
        out_dir,
    )
    asyncio.run(run(root, args, out_dir))


if __name__ == "__main__":
    main()
