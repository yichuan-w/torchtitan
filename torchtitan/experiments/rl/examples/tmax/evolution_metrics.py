# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Run-scoped evolution flow, sampled at each training step.

Every value is cumulative for this training run and counted when the trainer
observes it. Three of them form a cumulative flow diagram over training steps:
`signals_issued` (the trainer wrote the signal), `signals_closed` (the loop
wrote its ledger decision) and `rewrites_merged` (an accepted rewrite was
published into the mix). At one step, the vertical gap between issued and
closed is the work still open; at one height, the horizontal gap is how many
steps a signal waited. `merge_latency_steps` states that wait for merges.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout


SIGNAL_OUTCOMES = ("handled", "deferred", "superseded", "junk")
REWRITE_OUTCOMES = ("accepted_harder", "accepted_easier", "kept", "rejected", "failed")
FLOW_KEYS = ("signals_issued", "signals_closed", "rewrites_merged")
# Merges the latency median looks back over: recent enough to move when the
# loop speeds up or stalls, wide enough that one outlier does not set it.
LATENCY_WINDOW = 20


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


def _new_lines(path: Path, offset: int) -> tuple[list[dict], int]:
    """Complete JSON lines appended after ``offset``; a line the writer is
    still appending is left for the next poll."""
    if not path.exists():
        return [], offset
    events = []
    with path.open("rb") as stream:
        stream.seek(offset)
        while line := stream.readline():
            if not line.endswith(b"\n"):
                break
            events.append(json.loads(line))
            offset = stream.tell()
    return events, offset


class EvolutionMetrics:
    def __init__(self, run: layout.Run, root: layout.Root) -> None:
        self.run = run
        self.root = root
        self.claim_offset = 0
        self.ledger_offset = 0
        self.outcome_offset = 0
        # The policy version at group claim is the step a signal was issued at.
        self.claimed_origin: dict[int, int] = {}
        self.issued: dict[str, int] = {}
        self.closed: dict[str, str] = {}
        self.rewrites: set[str] = set()
        self.outcomes: Counter[str] = Counter()
        self.latencies: list[int] = []
        self.history: dict[int, tuple[int, int, int]] = {}

    def _poll_claims(self) -> None:
        events, self.claim_offset = _new_lines(
            self.run.trainer / "training_lineage/events.jsonl", self.claim_offset
        )
        for event in events:
            if event["event"] == "claimed":
                self.claimed_origin[event["group_id"]] = event[
                    "generator_policy_version"
                ]

    def _poll_signals(self) -> None:
        for path in self.run.signal_files():
            identity = f"{self.run.name}/{path.stem}"
            if identity in self.issued:
                continue
            origin = self.claimed_origin.get(json.loads(path.read_text())["group"])
            # Counted once its claim is visible, so its issue step is known.
            if origin is not None:
                self.issued[identity] = origin

    def _poll_ledger(self) -> None:
        events, self.ledger_offset = _new_lines(
            self.root.evolution.ledger, self.ledger_offset
        )
        for event in events:
            signal = event.get("signal", "")
            if signal.startswith(f"{self.run.name}/"):
                self.closed[signal] = event["outcome"]

    def _poll_outcomes(self, step: int | None) -> None:
        events, self.outcome_offset = _new_lines(
            self.root.evolution.run_outcomes(self.run.name), self.outcome_offset
        )
        for event in events:
            if event["rewrite"] in self.rewrites:
                continue
            self.rewrites.add(event["rewrite"])
            status = event["status"]
            if status == "accepted":
                self.outcomes[f"accepted_{event['direction']}"] += 1
                origin = self.issued.get(event.get("signal"))
                if step is not None and origin is not None:
                    self.latencies.append(step - origin)
            elif status == "interrupted":
                self.outcomes["failed"] += 1
            else:
                self.outcomes[status] += 1

    def poll(self, *, step: int | None = None) -> dict[str, float]:
        self._poll_claims()
        self._poll_signals()
        self._poll_ledger()
        self._poll_outcomes(step)
        closed = Counter(o for s, o in self.closed.items() if s in self.issued)
        flow = (
            len(self.issued),
            sum(closed.values()),
            self.outcomes["accepted_harder"] + self.outcomes["accepted_easier"],
        )
        if step is not None:
            self.history[step] = flow
        values = {
            **{f"evolution/flow/{k}": float(v) for k, v in zip(FLOW_KEYS, flow)},
            **{f"evolution/signals/{o}": float(closed[o]) for o in SIGNAL_OUTCOMES},
            **{
                f"evolution/rewrites/{o}": float(self.outcomes[o])
                for o in REWRITE_OUTCOMES
            },
        }
        if self.latencies:
            values["evolution/flow/merge_latency_steps"] = float(
                statistics.median(self.latencies[-LATENCY_WINDOW:])
            )
        return values

    def flow_series(self) -> tuple[list[int], list[list[int]], list[str]]:
        """The three flow counts at every logged step, for one overlay chart."""
        steps = sorted(self.history)
        return (
            steps,
            [[self.history[s][i] for s in steps] for i in range(len(FLOW_KEYS))],
            list(FLOW_KEYS),
        )
