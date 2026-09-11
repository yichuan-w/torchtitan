# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Prepare an isolated evolution compatibility probe from a passing reference trace.

The trace belongs to a scripted reference policy, not a student model. This
probe checks the evolution pipeline; it cannot measure learning or difficulty.
Run evolve_ondella.py --once against the emitted root to execute the rewrite.
"""

import argparse
import json
import os
import shutil
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout, new_root, rollout_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--reference-log", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    reference = json.loads(args.reference_log.read_text().splitlines()[-1])
    if not reference.get("ok") or reference.get("reward") != 1:
        raise ValueError("the probe requires a passing reference execution")
    if reference.get("execution_harness") != "terminus":
        raise ValueError("the reference must execute through Terminus")
    row = next(
        json.loads(line)
        for line in args.rows.read_text().splitlines()
        if json.loads(line)["metadata"]["instance_id"] == args.task
    )
    if args.out.exists():
        raise FileExistsError(args.out)
    args.out.mkdir(parents=True)
    seed = args.out / "reference-seed.jsonl"
    seed.write_text(json.dumps(row) + "\n")
    root = new_root.create(
        args.out,
        mix=seed,
        sources=[args.source],
        bin_dir=args.bin_dir,
        name=None,
        purpose="Reference-driven evolution compatibility probe; not a student measurement",
        profile=os.environ.get("TRL_PROFILE"),
        fork_from=None,
    )
    run = root.run("reference--" + layout.stamp())
    evidence = run.path / "inputs/reference.log"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.reference_log, evidence)
    turns = [
        {
            "turn": i + 1,
            "output": item["prompt"],
            **rollout_record.parse_completion(item["response"]),
        }
        for i, item in enumerate(reference["transcript"])
    ]
    record = run.rollout_record(args.task, 0, 0)
    rollout_record.write_record(
        record,
        {
            "task": args.task,
            "rev": 0,
            "run": run.name,
            "group": 0,
            "rollout": 0,
            "reward": 1,
            "turns": len(turns),
            "policy": "scripted_reference",
            "measurement_scope": "compatibility_only",
        },
        turns,
    )
    layout.write_json_atomic(
        run.signal(args.task, 0),
        {
            "task": args.task,
            "rev": 0,
            "run": run.name,
            "group": 0,
            "direction": "harder",
            "solved": 1,
            "total": 1,
            "created": layout.stamp(),
            "attempts": [str(record.relative_to(run.path))],
            "student_feedback": {
                "measurement_scope": (
                    "The trace is a scripted reference execution, not a student model. "
                    "Do not infer student difficulty or learning from it."
                ),
                "requested_adjustment": (
                    "Exercise a modest task extension to verify corpus compatibility "
                    "with the evolution, validation and fold pipeline."
                ),
            },
        },
    )
    print(
        json.dumps(
            {
                "root": str(root.path),
                "task": args.task,
                "measurement_scope": "compatibility_only",
            }
        )
    )


if __name__ == "__main__":
    main()
