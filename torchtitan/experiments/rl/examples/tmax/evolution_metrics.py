# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Run-scoped evolution outcomes sampled at each training step."""

from __future__ import annotations

import json
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout


COUNTERS = (
    "completed",
    "accepted",
    "harder_accepted",
    "easier_accepted",
    "failed",
    "interrupted",
    "rejected",
    "blocked",
    "kept",
)


def record_outcome(root: layout.Root, rewrite: layout.RewriteDir, meta: dict) -> None:
    if meta.get("dry") or not meta.get("signal"):
        return
    run_name = meta["signal"].split("/", 1)[0]
    layout.append_jsonl(
        root.evolution.run_outcomes(run_name),
        {
            "rewrite": str(rewrite.path.relative_to(root.path)),
            "task": meta["task"],
            "direction": meta["job"],
            "status": meta["status"],
            "finished": meta.get("finished"),
        },
    )


class EvolutionMetrics:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.seen: set[str] = set()
        self.totals = dict.fromkeys(COUNTERS, 0)

    def poll(self) -> dict[str, float]:
        delta = dict.fromkeys(COUNTERS, 0)
        if self.path.exists():
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                while line := stream.readline():
                    # The writer may still be appending; retry this line next step.
                    if not line.endswith(b"\n"):
                        break
                    event = json.loads(line)
                    identity = event["rewrite"]
                    if identity not in self.seen:
                        status = event["status"]
                        keys = ["completed", status]
                        if status == "accepted":
                            keys.append(f"{event['direction']}_accepted")
                        elif status == "interrupted":
                            keys.append("failed")
                        for key in keys:
                            delta[key] += 1
                            self.totals[key] += 1
                        self.seen.add(identity)
                    self.offset = stream.tell()
        return {
            **{f"evolution/step/{key}": float(value) for key, value in delta.items()},
            **{
                f"evolution/run/{key}_total": float(value)
                for key, value in self.totals.items()
            },
        }
