# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Run-scoped evolution flow, sampled at each training step.

Every value is cumulative for this training run, across its controller
attempts (``runs/<name>--attempt-*`` hold each lifetime's signals and outcomes;
``runs/<name>/trainer`` holds the lineage of all of them), and counted when the
trainer observes it. Three of them form a cumulative flow diagram over training steps:
`signals_issued` (the trainer wrote the signal), `signals_consumed` (the loop
wrote its ledger decision) and `rewrites_accepted` (a rewrite was accepted,
which means folded into the mix). At one step, the vertical gap between issued
and consumed is the work still open; at one height, the horizontal gap is how
many steps a signal waited. `accept_latency_steps` states that wait for
accepted rewrites. `rewrites_trained` counts accepted rewrites whose new
revision has been trained on at least once, and `train_latency_steps` is the
wait from a signal's issue to that first training step: an accepted revision
reaches training only when the epoch order next reaches its task.

The two signal directions mean opposite things -- `harder` comes from a group
that all passed, `easier` from one that all failed -- and move opposite ways as
the policy learns, so each direction also gets its own four curves
(`<key>_<direction>`, one overlay chart per direction). The counts at each
step are appended to the run's `trainer/evolution_flow.jsonl` and read back
when a trainer resumes, so the charts keep the steps before the resume. A
rewrite already accepted when this process first polls was accepted at a step
it cannot know, and enters no `accept_latency_steps`. A signal's direction
follows every stage, so a rewrite counts under the direction of the signal
that asked for it whatever route handled it.

`stale/groups` counts the groups trained on a task's original while its
rewrite was pending: claimed after one of this run's signals asked to rewrite
that exact revision and before the loop handled the signal. Such a group most
likely repeats the all-pass or all-fail result the signal reported, so this is
training spent on a revision already known to carry no signal while the loop
caught up. Only signals the loop handled count; one it deferred, superseded or
judged junk was never going to be rewritten from that wait. Each signal is
counted when it is handled, so the count only grows.

`repairs/*` counts the defects harder and easier rewrites repaired on the way
(declared in run/repair.json; one line each in evolution/repairs.jsonl):
`rewrites` that repaired anything, the `fixes` they declared, how many of
those rewrites were accepted, the rewrites per signal direction, and the
fixes per declared kind (`kind_reward_hack`, `kind_hidden_requirement`,
`kind_broken_setup`, `kind_other`). `repair_rows` keeps each fix with the
rewriter's own defect, evidence and change, for the `evolution/repairs/report`
table.

`report_rows` is one row per rewrite for the `evolution/rewrites/report` table:
what training measured before it (the signal's group), what the rewriter says
it did (an easier rewrite's retained skill, change and expected count, or the
opening of a harder rewrite's notes), and, for an accepted rewrite, the groups
training has since finished on the revision it produced.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout


SIGNAL_OUTCOMES = ("handled", "deferred", "superseded", "resolved", "junk")
REWRITE_OUTCOMES = (
    "accepted_harder", "accepted_easier", "accepted_repair_spec", "kept", "rejected", "failed"
)
REWRITE_ROUTES = ("evolve", "simplify", "repair_spec", "invalid_signal")
REPAIR_KEYS = ("rewrites", "fixes", "accepted_rewrites", "harder", "easier")
# What a declared repair fixed; evolve_codex._read_repairs requires one per fix.
REPAIR_KINDS = ("reward_hack", "hidden_requirement", "broken_setup", "other")
REPAIR_COLUMNS = ("step", "task", "direction", "status", "kind", "defect", "evidence", "change")
REWRITE_COLUMNS = (
    "step", "task", "direction", "status", "stage", "revs", "before", "expected", "after",
    "retained_skill", "change", "reason",
)
# Each text cell is cut to this many characters: the table is uploaded whole.
REPORT_CHARS = 600
# Groups shown in "after"; the solved fraction covers every finished group.
AFTER_GROUPS = 6
# Training steps between uploads of the rewrite report: its rows change as
# groups finish, and every upload is the whole table.
REWRITE_REPORT_EVERY = 10
# The runbook's table of evolution keys is the one place each is described; the
# trainer uploads it as the evolution/glossary table so W&B shows it too.
RUNBOOK = Path(__file__).parent / "runbook/RUNBOOK.md"
GLOSSARY_SECTION = "### Evolution charts in the training W&B run"
GLOSSARY_COLUMNS = ("metric", "description")
FLOW_KEYS = ("signals_issued", "signals_consumed", "rewrites_accepted", "rewrites_trained")
DIRECTIONS = ("harder", "easier")
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
            "route": meta.get("route") or (
                "repair_spec" if meta.get("spec_repair") else
                "invalid_signal" if meta.get("stage") == "invalid_signal" else
                "simplify" if meta["job"] == "easier" else "evolve"
            ),
            "stage": meta.get("stage"),
            "started": meta.get("started"),
            "finished": meta.get("finished"),
            "signal": meta["signal"],
            "repairs": len(meta.get("repair") or []),
            "repair": [
                {k: r.get(k) for k in ("kind", "defect", "evidence", "change")}
                for r in meta.get("repair") or []
            ],
            "input_rev": meta.get("input_rev"),
            "result_rev": meta.get("result_rev"),
            "reason": meta.get("reason"),
            "summary": _summary(rewrite, meta),
        },
    )


def _summary(rewrite: layout.RewriteDir, meta: dict) -> dict:
    """What the rewriter says it did: an easier rewrite's declaration, or the
    opening of a harder rewrite's notes (kept beside the rewrite by the fold)."""
    decision = meta.get("simplify") or {}
    note = ""
    for path in (rewrite.notes / "hardening.md", rewrite.package / "run/hardening.md"):
        if path.is_file():
            note = path.read_text(errors="replace")[:REPORT_CHARS]
            break
    return {
        "retained_skill": decision.get("retained_skill"),
        "change": decision.get("change") or note or None,
        "expected": decision.get("expected"),
    }


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
        self.base = run.base
        self.root = root
        self.claim_offset = 0
        self.ledger_offset = 0
        self.outcome_offsets: dict[str, int] = {}
        # The policy version at group claim is the step a signal was issued at.
        # Group ids restart with every controller attempt, so a claim is keyed by
        # its lineage session; (None, group) is the latest claim of that group,
        # for a run without attempts.
        self.claimed_origin: dict[tuple[str | None, int], int] = {}
        self.issued: dict[str, int] = {}
        self.signal_direction: dict[str, str] = {}
        self.closed: dict[str, str] = {}
        self.rewrites: set[str] = set()
        self.outcomes: Counter[str] = Counter()
        self.routes: dict[str, Counter[str]] = {route: Counter() for route in REWRITE_ROUTES}
        self.latencies: list[int] = []
        self.accepted_signals: list[str] = []
        # Why rewrites ended where they did, and how long each took.
        self.stages: Counter[tuple[str, str]] = Counter()
        self.rewrite_secs: list[float] = []
        # Flow counts at each logged step: None for all signals, else by
        # direction. Read back from the run, so a resumed trainer charts the
        # steps of the lifetimes before it too.
        self.history: dict[int, dict[str | None, tuple[int, ...]]] = self._load_history()
        # The first step this process logs. An outcome already on disk by then
        # was accepted at a step this process cannot know (before the resume,
        # or by an earlier attempt), so it enters no latency and no report step.
        self.first_step: int | None = None
        # First training on an accepted revision: trained groups by task, and
        # accepted rewrites still waiting for one.
        self.trained: dict[str, list[tuple[float, str, int]]] = {}
        self.awaiting_training: dict[str, tuple[str, str, float, int, str]] = {}
        self.train_latencies: list[int] = []
        self.trained_signals: list[str] = []
        # Staleness: every claim by task, each signal's revision and lifetime.
        self.claims: dict[str, list[tuple[float, str, str]]] = {}
        self.group_revision: dict[tuple[str | None, int], str] = {}
        self.signal_span: dict[str, tuple[str, str, float]] = {}
        self.closed_at: dict[str, float] = {}
        self.classified: set[str] = set()
        self.stale_claims: set[str] = set()
        self.stale_groups = 0
        self.repairs: Counter[str] = Counter()
        # One row per declared repair, for the report table beside the counts.
        self.repair_rows: list[list] = []
        # Rewrite report: every outcome line, each signal's measured group, and
        # every finished group by task as (time, revision, solved, scored).
        self.rewrite_events: list[tuple[int | None, dict]] = []
        self.signal_measured: dict[str, str] = {}
        self.finished_groups: dict[str, list[tuple[float, str, int, int]]] = {}

    def _load_history(self) -> dict[int, dict[str | None, tuple[int, ...]]]:
        history = {}
        # A later line for the same step is the lifetime that logged it again.
        for line in _new_lines(self.base.evolution_flow, 0)[0]:
            history[line["step"]] = {
                None if key == "all" else key: tuple(counts)
                for key, counts in line["flow"].items()
            }
        return history

    def _poll_claims(self) -> None:
        events, self.claim_offset = _new_lines(
            self.base.trainer / "training_lineage/events.jsonl", self.claim_offset
        )
        for event in events:
            if (
                event["event"] == "finalized"
                and "sample_revision" in event
                and event.get("num_scored_rollouts")
            ):
                self.finished_groups.setdefault(event["task_id"], []).append(
                    (
                        event["time_unix_ns"] / 1e9,
                        event["sample_revision"],
                        int(event.get("num_solved") or 0),
                        int(event["num_scored_rollouts"]),
                    )
                )
            if event["event"] == "trained" and "sample_revision" in event:
                self.trained.setdefault(event["task_id"], []).append(
                    (
                        event["time_unix_ns"] / 1e9,
                        event["sample_revision"],
                        event["train_step"],
                    )
                )
            if event["event"] == "claimed":
                keys = ((event.get("session_id"), event["group_id"]), (None, event["group_id"]))
                for key in keys:
                    self.claimed_origin[key] = event["generator_policy_version"]
                # Lineage written before claims carried their sample is skipped.
                if "sample_revision" in event and "task_id" in event:
                    for key in keys:
                        self.group_revision[key] = event["sample_revision"]
                    self.claims.setdefault(event["task_id"], []).append(
                        (
                            event["time_unix_ns"] / 1e9,
                            event["sample_revision"],
                            event["occurrence_id"],
                        )
                    )

    def _poll_signals(self) -> None:
        for attempt in self.base.attempts():
            for path in attempt.signal_files():
                self._poll_signal(attempt, path)

    def _poll_signal(self, attempt: layout.Run, path: Path) -> None:
        identity = f"{attempt.name}/{path.stem}"
        if identity in self.issued:
            return
        signal = json.loads(path.read_text())
        if signal.get("total") is not None:
            self.signal_measured[identity] = f"{signal.get('solved')}/{signal['total']}"
        key = (attempt.session_id, signal["group"])
        origin = self.claimed_origin.get(key)
        # Counted once its claim is visible, so its issue step is known.
        if origin is not None:
            self.issued[identity] = origin
            self.signal_direction[identity] = signal.get("direction") or "unknown"
            revision = self.group_revision.get(key)
            if revision is not None and signal.get("created"):
                self.signal_span[identity] = (
                    signal["task"],
                    revision,
                    layout.parse_stamp(signal["created"]),
                )

    def _poll_ledger(self) -> None:
        events, self.ledger_offset = _new_lines(
            self.root.evolution.ledger, self.ledger_offset
        )
        for event in events:
            signal = event.get("signal", "")
            if self._ours(signal):
                self.closed[signal] = event["outcome"]
                if event.get("stamp"):
                    self.closed_at.setdefault(signal, layout.parse_stamp(event["stamp"]))

    def _ours(self, signal: str) -> bool:
        """A signal id names the attempt directory that issued it."""
        name, slash, _ = signal.partition("/")
        return bool(slash) and layout.Run(self.base.path.with_name(name)).base == self.base

    def _poll_outcomes(self, step: int | None) -> None:
        events = []
        for attempt in self.base.attempts():
            new, self.outcome_offsets[attempt.name] = _new_lines(
                self.root.evolution.run_outcomes(attempt.name),
                self.outcome_offsets.get(attempt.name, 0),
            )
            events += new
        for event in events:
            if event["rewrite"] in self.rewrites:
                continue
            self.rewrites.add(event["rewrite"])
            self.rewrite_events.append((step, event))
            status = event["status"]
            if fixes := int(event.get("repairs") or 0):
                self.repairs["rewrites"] += 1
                self.repairs["fixes"] += fixes
                self.repairs[event["direction"]] += 1
                for repair in event.get("repair") or []:
                    self.repairs[f"kind_{repair.get('kind')}"] += 1
                    self.repair_rows.append(
                        [step, event["task"], event["direction"], status,
                         *(repair.get(k) for k in REPAIR_COLUMNS[4:])]
                    )
                if status == "accepted":
                    self.repairs["accepted_rewrites"] += 1
            self.stages[("failed" if status == "interrupted" else status, event.get("stage") or "none")] += 1
            if event.get("started") and event.get("finished"):
                try:
                    self.rewrite_secs.append(
                        layout.parse_stamp(event["finished"]) - layout.parse_stamp(event["started"])
                    )
                except (TypeError, ValueError):
                    pass
            route = event.get("route") or ("simplify" if event["direction"] == "easier" else "evolve")
            if route in self.routes:
                self.routes[route]["attempted"] += 1
                self.routes[route]["failed" if status == "interrupted" else status] += 1
            if status == "accepted":
                outcome = "accepted_repair_spec" if route == "repair_spec" else f"accepted_{event['direction']}"
                self.outcomes[outcome] += 1
                self.accepted_signals.append(event.get("signal"))
                origin = self.issued.get(event.get("signal"))
                if step is not None and origin is not None:
                    self.latencies.append(step - origin)
                span = self.signal_span.get(event.get("signal"))
                if origin is not None and span is not None and event.get("finished"):
                    self.awaiting_training[event["rewrite"]] = (
                        span[0],
                        span[1],
                        layout.parse_stamp(event["finished"]),
                        origin,
                        event["signal"],
                    )
            elif status == "interrupted":
                self.outcomes["failed"] += 1
            else:
                self.outcomes[status] += 1

    def _classify_stale(self) -> None:
        for signal, closed_at in self.closed_at.items():
            if (
                signal in self.classified
                or signal not in self.signal_span
                or self.closed.get(signal) != "handled"
            ):
                continue
            self.classified.add(signal)
            task, revision, created = self.signal_span[signal]
            for claimed_at, claim_revision, occurrence in self.claims.get(task, ()):
                if (
                    claim_revision != revision
                    or not created <= claimed_at <= closed_at
                    or occurrence in self.stale_claims
                ):
                    continue
                self.stale_claims.add(occurrence)
                self.stale_groups += 1

    def _match_training(self) -> None:
        """An accepted rewrite reaches training at the first trained group of
        its task on a revision other than the one its signal measured."""
        for rewrite, (task, old, accepted_at, origin, signal) in list(
            self.awaiting_training.items()
        ):
            first = min(
                (
                    train_step
                    for trained_at, revision, train_step in self.trained.get(task, ())
                    if revision != old and trained_at >= accepted_at
                ),
                default=None,
            )
            if first is not None:
                self.train_latencies.append(first - origin)
                self.trained_signals.append(signal)
                del self.awaiting_training[rewrite]

    def poll(self, *, step: int | None = None) -> dict[str, float]:
        self._poll_claims()
        self._poll_signals()
        self._poll_ledger()
        observed = step if self.first_step is not None else None
        if step is not None and self.first_step is None:
            self.first_step = step
            # Steps from here on were logged by a lifetime whose checkpoint
            # this one did not resume from; this one logs them again.
            self.history = {s: h for s, h in self.history.items() if s < step}
        self._poll_outcomes(observed)
        self._classify_stale()
        self._match_training()
        closed = Counter(o for s, o in self.closed.items() if s in self.issued)
        flow = (
            len(self.issued),
            sum(closed.values()),
            self.outcomes["accepted_harder"] + self.outcomes["accepted_easier"] + self.outcomes["accepted_repair_spec"],
            len(self.train_latencies),
        )
        by_direction = {
            d: (
                sum(v == d for v in self.signal_direction.values()),
                sum(self.signal_direction[s] == d for s in self.closed if s in self.issued),
                sum(self.signal_direction.get(s) == d for s in self.accepted_signals),
                sum(self.signal_direction.get(s) == d for s in self.trained_signals),
            )
            for d in DIRECTIONS
        }
        if step is not None:
            self.history[step] = {None: flow, **by_direction}
            layout.append_jsonl(
                self.base.evolution_flow,
                {
                    "step": step,
                    "flow": {
                        "all" if key is None else key: list(counts)
                        for key, counts in self.history[step].items()
                    },
                },
            )
        values = {
            **{f"evolution/flow/{k}": float(v) for k, v in zip(FLOW_KEYS, flow, strict=True)},
            **{f"evolution/signals/{o}": float(closed[o]) for o in SIGNAL_OUTCOMES},
            **{
                f"evolution/rewrites/{o}": float(self.outcomes[o])
                for o in REWRITE_OUTCOMES
            },
            **{f"evolution/repairs/{k}": float(self.repairs[k]) for k in REPAIR_KEYS},
            **{f"evolution/repairs/kind_{k}": float(self.repairs[f"kind_{k}"]) for k in REPAIR_KINDS},
            "evolution/stale/groups": float(self.stale_groups),
            **{
                f"evolution/{route}/{outcome}": float(counts[outcome])
                for route, counts in self.routes.items()
                for outcome in ("attempted", "accepted", "kept", "rejected", "failed")
            },
        }
        for direction, counts in by_direction.items():
            for key, count in zip(FLOW_KEYS, counts, strict=True):
                values[f"evolution/flow/{key}_{direction}"] = float(count)
        # A signal written without a direction still shows up as its own count.
        unknown = sum(v not in DIRECTIONS for v in self.signal_direction.values())
        if unknown:
            values["evolution/flow/signals_issued_unknown"] = float(unknown)
        for (status, stage), count in self.stages.items():
            values[f"evolution/rewrites_by_stage/{status}/{stage}"] = float(count)
        if self.rewrite_secs:
            values["evolution/flow/rewrite_secs"] = float(
                statistics.median(self.rewrite_secs[-LATENCY_WINDOW:])
            )
        for route, counts in self.routes.items():
            if counts["attempted"]:
                values[f"evolution/{route}/acceptance_rate"] = counts["accepted"] / counts["attempted"]
        if self.latencies:
            values["evolution/flow/accept_latency_steps"] = float(
                statistics.median(self.latencies[-LATENCY_WINDOW:])
            )
        if self.train_latencies:
            values["evolution/flow/train_latency_steps"] = float(
                statistics.median(self.train_latencies[-LATENCY_WINDOW:])
            )
        return values

    def report_rows(self) -> list[list]:
        """One row per rewrite, in the order the loop finished them."""
        rows = []
        for step, event in self.rewrite_events:
            summary = event.get("summary") or {}
            signal = event.get("signal")
            rows.append([
                step,
                event["task"],
                event["direction"],
                event["status"],
                event.get("stage"),
                f"r{event.get('input_rev')}->r{event.get('result_rev')}"
                if event["status"] == "accepted" else f"r{event.get('input_rev')}",
                self.signal_measured.get(signal),
                summary.get("expected"),
                self._after(event) if event["status"] == "accepted" else None,
                _cut(summary.get("retained_skill")),
                _cut(summary.get("change")),
                _cut(event.get("reason")),
            ])
        return rows

    def _after(self, event: dict) -> str | None:
        """Groups training finished on the revision an accepted rewrite
        produced: the first revision of its task, other than the one its
        signal measured, finished after the rewrite was accepted."""
        span = self.signal_span.get(event.get("signal"))
        if span is None or not event.get("finished"):
            return None
        task, old, _ = span
        accepted_at = layout.parse_stamp(event["finished"])
        groups = [g for g in self.finished_groups.get(task, ()) if g[0] >= accepted_at and g[1] != old]
        if not groups:
            return None
        revision = groups[0][1]
        groups = [g for g in groups if g[1] == revision]
        shown = ", ".join(f"{g[2]}/{g[3]}" for g in groups[:AFTER_GROUPS])
        more = f", ... ({len(groups)} groups)" if len(groups) > AFTER_GROUPS else ""
        solved = sum(g[2] for g in groups) / sum(g[3] for g in groups)
        return f"{shown}{more}; solved {solved:.2f}"

    def flow_series(
        self, direction: str | None = None
    ) -> tuple[list[int], list[list[int]], list[str]]:
        """The flow counts at every logged step, for one overlay chart: all
        signals, or only those of one direction."""
        steps = sorted(self.history)
        return (
            steps,
            [[self.history[s][direction][i] for s in steps] for i in range(len(FLOW_KEYS))],
            list(FLOW_KEYS),
        )


def glossary_rows(runbook: Path = RUNBOOK) -> list[list[str]]:
    """One row per line of the runbook's evolution key table, backticks dropped."""
    section = runbook.read_text().split(GLOSSARY_SECTION, 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if line.startswith("| `"):
            key, description = line.strip().strip("|").split(" | ", 1)
            rows.append([key.strip().replace("`", ""), description.strip().replace("`", "")])
    return rows


def _cut(text: object) -> str | None:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= REPORT_CHARS else text[:REPORT_CHARS] + "..."
