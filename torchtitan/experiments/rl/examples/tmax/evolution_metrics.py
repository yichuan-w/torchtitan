# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Run-scoped evolution outcomes sampled at each training step."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import accumulate
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout


COUNTERS = (
    "rewrite_outcomes",
    "accepted",
    "harder_accepted",
    "easier_accepted",
    "failed",
    "interrupted",
    "rejected",
    "blocked",
    "kept",
)
ORIGIN_CHART_COUNTERS = (
    "harder_accepted",
    "accepted",
    "failed",
    "rejected",
    "rewrite_outcomes",
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
            "signal": meta["signal"],
        },
    )


class EvolutionMetrics:
    def __init__(
        self,
        path: Path,
        *,
        run: layout.Run | None = None,
        root: layout.Root | None = None,
    ) -> None:
        self.path = path
        self.run = run
        self.root = root
        self.offset = 0
        self.claim_offset = 0
        self.ledger_offset = 0
        self.seen: set[str] = set()
        self.seen_signals: set[str] = set()
        self.totals = dict.fromkeys(COUNTERS, 0)
        self.claimed_origin: dict[int, int] = {}
        self.signal_origin: dict[str, int] = {}
        self.issue_signals: Counter[int] = Counter()
        self.consumed_signals: set[str] = set()
        self.outcomes_by_origin: dict[int, Counter[str]] = defaultdict(Counter)
        self.observed_by_step: dict[int, Counter[str]] = defaultdict(Counter)
        self.rewrite_outcomes_total_by_step: dict[int, int] = {}
        self.pending_origin: dict[str, dict] = {}

    def _poll_claims(self) -> None:
        if self.run is None:
            return
        path = self.run.trainer / "training_lineage/events.jsonl"
        if not path.exists():
            return
        with path.open("rb") as stream:
            stream.seek(self.claim_offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                event = json.loads(line)
                if event["event"] == "claimed":
                    # The policy version at group claim is the origin of its issue.
                    self.claimed_origin[event["group_id"]] = event[
                        "generator_policy_version"
                    ]
                self.claim_offset = stream.tell()

    def _poll_signals(self) -> None:
        if self.run is None:
            return
        for path in self.run.signal_files():
            if path.name in self.seen_signals:
                continue
            signal = json.loads(path.read_text())
            origin = self.claimed_origin.get(signal["group"])
            if origin is None:
                continue
            identity = f"{self.run.name}/{path.stem}"
            self.signal_origin[identity] = origin
            self.issue_signals[origin] += 1
            self.seen_signals.add(path.name)

    def _poll_ledger(self) -> None:
        if self.root is None or self.run is None:
            return
        path = self.root.evolution.ledger
        if not path.exists():
            return
        with path.open("rb") as stream:
            stream.seek(self.ledger_offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                signal = json.loads(line).get("signal", "")
                if signal.startswith(f"{self.run.name}/"):
                    self.consumed_signals.add(signal)
                self.ledger_offset = stream.tell()

    def _record_origin(self, event: dict) -> bool:
        signal = event.get("signal")
        if signal is None and self.root is not None:
            # Older outcome records did not carry the signal ID; their rewrite does.
            meta = self.root.path / event["rewrite"] / "rewrite.json"
            if meta.exists():
                signal = json.loads(meta.read_text()).get("signal")
        origin = self.signal_origin.get(signal)
        if origin is None:
            return False
        status = event["status"]
        self.outcomes_by_origin[origin]["rewrite_outcomes"] += 1
        self.outcomes_by_origin[origin][status] += 1
        if status == "accepted":
            self.outcomes_by_origin[origin][f"{event['direction']}_accepted"] += 1
        if status == "interrupted":
            self.outcomes_by_origin[origin]["failed"] += 1
        return True

    def signal_flow_series(
        self, step: int
    ) -> tuple[list[int], list[list[int]], list[str]]:
        """Compare origin-attributed signals with observed rewrite outcomes."""
        xs = list(range(max([step, *self.issue_signals]) + 1))
        consumed = Counter(
            self.signal_origin[signal]
            for signal in self.consumed_signals
            if signal in self.signal_origin
        )
        ys = [
            list(accumulate(self.issue_signals[x] for x in xs)),
            list(accumulate(consumed[x] for x in xs)),
            [self.rewrite_outcomes_total_by_step.get(x, 0) for x in xs],
        ]
        return (
            xs,
            ys,
            [
                "signal_issued (origin cumulative)",
                "signal_consumed (origin cumulative)",
                "rewrite_outcomes (observed cumulative)",
            ],
        )

    def comparison_series(
        self, step: int, counter: str
    ) -> tuple[list[int], list[list[int]], list[str]]:
        """Pair an existing per-step outcome curve with its origin-step curve."""
        if counter not in ORIGIN_CHART_COUNTERS:
            raise ValueError(f"unsupported evolution counter: {counter}")
        xs = list(range(max([step, *self.issue_signals]) + 1))
        ys = [
            [self.observed_by_step[x][counter] for x in xs],
            [self.outcomes_by_origin[x][counter] for x in xs],
        ]
        return xs, ys, ["observed at training step", "origin policy step"]

    def poll(self, *, step: int | None = None) -> dict[str, float]:
        self._poll_claims()
        self._poll_signals()
        self._poll_ledger()
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
                        keys = ["rewrite_outcomes", status]
                        if status == "accepted":
                            keys.append(f"{event['direction']}_accepted")
                        elif status == "interrupted":
                            keys.append("failed")
                        for key in keys:
                            delta[key] += 1
                            self.totals[key] += 1
                        self.seen.add(identity)
                        if not self._record_origin(event):
                            self.pending_origin[identity] = event
                    self.offset = stream.tell()
        for identity, event in list(self.pending_origin.items()):
            if self._record_origin(event):
                del self.pending_origin[identity]
        if step is not None:
            self.observed_by_step[step].update(delta)
            self.rewrite_outcomes_total_by_step[step] = self.totals["rewrite_outcomes"]
        return {
            **{f"evolution/step/{key}": float(value) for key, value in delta.items()},
            **{
                f"evolution/run/{key}_total": float(value)
                for key, value in self.totals.items()
            },
            **(
                {
                    "evolution/run/signal_issued_total": float(len(self.seen_signals)),
                    "evolution/run/signal_consumed_total": float(
                        len(self.consumed_signals & self.signal_origin.keys())
                    ),
                }
                if self.run is not None
                else {}
            ),
            **(
                {
                    "evolution/origin/unmapped_outcomes_total": float(
                        len(self.pending_origin)
                    )
                }
                if self.run is not None
                else {}
            ),
        }
