#!/usr/bin/env python3
"""Inspect the exact task/revision flow through asynchronous RL training.

The training controller writes append-only lifecycle events; evolve_ondella
writes append-only retune/fold events. This tool joins them without modifying
either source. Its four views answer: what trained in a step, what happened to a
task, whether lifecycle invariants hold, and how data/evolution moved overall.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_EVENTS = Path(
    os.environ.get(
        "TRAINING_LINEAGE_EVENTS",
        "outputs/rl/training_lineage/events.jsonl",
    )
)
DEFAULT_EVOLUTION_EVENTS = os.environ.get("SWE_EVOLUTION_LINEAGE", "")
_STAGES = ("admitted", "claimed", "finalized", "selected", "packed", "trained")


def load_jsonl(path: Path | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Load valid records while reporting malformed lines for the audit view."""
    if path is None or not path.exists():
        return [], []
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open() as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{line_number}: malformed JSON: {exc.msg}")
                continue
            if not isinstance(record, dict):
                errors.append(f"{path}:{line_number}: record is not an object")
                continue
            record["_source_file"] = str(path)
            record["_source_line"] = line_number
            records.append(record)
    return records, errors


def _time_ns(record: dict[str, Any]) -> int:
    value = record.get("time_unix_ns") or record.get("observed_time_unix_ns") or 0
    return int(value) if isinstance(value, (int, float)) else 0


def _clean(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _occurrences(events: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        occurrence_id = event.get("occurrence_id")
        if occurrence_id:
            grouped[str(occurrence_id)].append(event)
    for records in grouped.values():
        records.sort(key=_time_ns)
    return grouped


def step_view(events: list[dict[str, Any]], train_step: int) -> dict[str, Any]:
    relevant = [
        event
        for event in events
        if event.get("train_step") == train_step
        or event.get("reserved_train_step") == train_step
    ]
    grouped = _occurrences(relevant)
    groups = []
    for occurrence_id, records in sorted(grouped.items()):
        last = records[-1]
        groups.append(
            {
                "occurrence_id": occurrence_id,
                "group_id": last.get("group_id"),
                "task_id": last.get("task_id"),
                "sample_revision": last.get("sample_revision"),
                "mix_revision": last.get("mix_revision"),
                "dataset_epoch": last.get("dataset_epoch"),
                "dataset_position": last.get("dataset_position"),
                "events": [record.get("event") for record in records],
            }
        )
    return {"train_step": train_step, "num_groups": len(groups), "groups": groups}


def task_view(
    events: list[dict[str, Any]],
    evolution_events: list[dict[str, Any]],
    task_id: str,
) -> dict[str, Any]:
    timeline = [event for event in events if event.get("task_id") == task_id]
    timeline.extend(
        event for event in evolution_events if event.get("task_id") == task_id
    )
    for event in events:
        if event.get("event") != "hot_reload":
            continue
        for change in event.get("changes", []):
            if change.get("task_id") == task_id:
                timeline.append(
                    {
                        **event,
                        "event": "hot_reload_task_change",
                        **change,
                    }
                )
    timeline.sort(key=_time_ns)
    return {"task_id": task_id, "events": [_clean(event) for event in timeline]}


def _reload_transitions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") != "hot_reload":
            continue
        observed_ns = int(event.get("observed_time_unix_ns") or _time_ns(event))
        for change in event.get("changes", []):
            if change.get("change") not in {"replaced", "appended"}:
                continue
            transitions.append(
                {
                    **change,
                    "observed_time_unix_ns": observed_ns,
                    "mix_revision": event.get("mix_revision"),
                }
            )
    return transitions


def _first_event(
    events: list[dict[str, Any]],
    *,
    task_id: str,
    revision: str,
    event_name: str,
    after_ns: int,
) -> dict[str, Any] | None:
    matches = [
        event
        for event in events
        if event.get("event") == event_name
        and event.get("task_id") == task_id
        and event.get("sample_revision") == revision
        and _time_ns(event) >= after_ns
    ]
    return min(matches, key=_time_ns) if matches else None


def _duration_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean_sec": statistics.fmean(values) if values else None,
        "median_sec": statistics.median(values) if values else None,
        "max_sec": max(values) if values else None,
    }


def _number_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "max": max(values) if values else None,
    }


def summary_view(
    events: list[dict[str, Any]], evolution_events: list[dict[str, Any]]
) -> dict[str, Any]:
    admitted = [event for event in events if event.get("event") == "admitted"]
    trained = [event for event in events if event.get("event") == "trained"]
    dropped = [event for event in events if event.get("event") == "dropped"]
    finalized = [event for event in events if event.get("event") == "finalized"]
    task_counts = Counter(str(event.get("task_id")) for event in admitted)
    epoch_rows: dict[str, dict[str, Any]] = {}
    for epoch, epoch_events in _group_by(admitted, "dataset_epoch").items():
        epoch_rows[str(epoch)] = {
            "occurrences": len(epoch_events),
            "unique_tasks": len({event.get("task_id") for event in epoch_events}),
            "unique_revisions": len(
                {event.get("sample_revision") for event in epoch_events}
            ),
        }

    terminal_by_occurrence: dict[str, str] = {}
    for event in dropped + trained:
        occurrence_id = event.get("occurrence_id")
        if occurrence_id:
            terminal_by_occurrence[str(occurrence_id)] = str(event.get("event"))
    durations: dict[str, list[float]] = defaultdict(list)
    for event in finalized:
        duration = event.get("rollout_duration_sec")
        if not isinstance(duration, (int, float)):
            continue
        terminal = terminal_by_occurrence.get(str(event.get("occurrence_id")), "pending")
        durations[terminal].append(float(duration))

    activations = []
    for transition in _reload_transitions(events):
        task_id = str(transition["task_id"])
        new_revision = str(transition["sample_revision"])
        observed_ns = int(transition["observed_time_unix_ns"])
        first_admitted = _first_event(
            events,
            task_id=task_id,
            revision=new_revision,
            event_name="admitted",
            after_ns=observed_ns,
        )
        first_trained = _first_event(
            events,
            task_id=task_id,
            revision=new_revision,
            event_name="trained",
            after_ns=observed_ns,
        )
        old_revision = transition.get("previous_sample_revision")
        queued_old_trained = sum(
            event.get("event") == "trained"
            and event.get("task_id") == task_id
            and event.get("sample_revision") == old_revision
            and _time_ns(event) >= observed_ns
            for event in events
        )
        folds = [
            event
            for event in evolution_events
            if event.get("event") == "folded"
            and event.get("task_id") == task_id
            and event.get("sample_revision") == new_revision
            and _time_ns(event) <= observed_ns
        ]
        latest_fold = max(folds, key=_time_ns) if folds else None
        activations.append(
            {
                "task_id": task_id,
                "previous_sample_revision": old_revision,
                "sample_revision": new_revision,
                "fold_to_reload_sec": (
                    (observed_ns - _time_ns(latest_fold)) / 1e9
                    if latest_fold
                    else None
                ),
                "reload_to_first_admitted_sec": (
                    (_time_ns(first_admitted) - observed_ns) / 1e9
                    if first_admitted
                    else None
                ),
                "reload_to_first_trained_sec": (
                    (_time_ns(first_trained) - observed_ns) / 1e9
                    if first_trained
                    else None
                ),
                "old_revision_groups_trained_after_reload": queued_old_trained,
            }
        )

    return {
        "counts": {
            "admitted_occurrences": len(admitted),
            "unique_tasks": len(task_counts),
            "repeated_occurrences": sum(count - 1 for count in task_counts.values()),
            "trained_groups": len(trained),
            "dropped_groups": len(dropped),
            "hot_reloads": sum(event.get("event") == "hot_reload" for event in events),
            "retunes_finished": sum(
                event.get("event") == "retune_finished" for event in evolution_events
            ),
            "folds": sum(event.get("event") == "folded" for event in evolution_events),
        },
        "drop_reasons": dict(Counter(str(event.get("reason")) for event in dropped)),
        "solve_classes_selected": dict(
            Counter(
                str(event.get("solve_class"))
                for event in events
                if event.get("event") == "selected"
            )
        ),
        "evolution_signal_outcomes": dict(
            Counter(
                "all_failed"
                if event.get("solved") == 0
                else "all_solved"
                if event.get("solved") == event.get("total")
                else "other"
                for event in evolution_events
                if event.get("event") == "retune_finished"
            )
        ),
        "rollout_duration_by_terminal_state": {
            state: _duration_summary(values) for state, values in sorted(durations.items())
        },
        "bypass_count": _number_summary(
            [float(event.get("bypass_count", 0)) for event in finalized]
        ),
        "epoch_coverage": epoch_rows,
        "evolution_activation": activations,
    }


def _group_by(
    records: Iterable[dict[str, Any]], key: str
) -> dict[Any, list[dict[str, Any]]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record.get(key)].append(record)
    return grouped


def audit_view(
    events: list[dict[str, Any]], load_errors: list[str]
) -> dict[str, Any]:
    errors = list(load_errors)
    warnings: list[str] = []
    occurrences = _occurrences(events)
    for occurrence_id, records in occurrences.items():
        names = [str(record.get("event")) for record in records]
        counts = Counter(names)
        for stage in (*_STAGES, "dropped"):
            if counts[stage] > 1:
                errors.append(f"{occurrence_id}: duplicate {stage} events")
        if "admitted" not in counts:
            errors.append(f"{occurrence_id}: missing admitted event")
        if (
            "claimed" in counts
            and "admitted" in counts
            and names.index("claimed") < names.index("admitted")
        ):
            errors.append(f"{occurrence_id}: claimed before admitted")
        if "finalized" in counts and "claimed" not in counts:
            errors.append(f"{occurrence_id}: finalized without claimed")
        if "selected" in counts and "finalized" not in counts:
            errors.append(f"{occurrence_id}: selected without finalized")
        if "trained" in counts and "packed" not in counts:
            errors.append(f"{occurrence_id}: trained without packed")
        if "trained" in counts and "dropped" in counts:
            errors.append(f"{occurrence_id}: both trained and dropped")
        if "trained" not in counts and "dropped" not in counts:
            warnings.append(f"{occurrence_id}: no terminal trained/dropped event")
        ordered_pairs = (
            ("admitted", "claimed"),
            ("claimed", "finalized"),
            ("finalized", "selected"),
            ("selected", "packed"),
            ("packed", "trained"),
        )
        for earlier, later in ordered_pairs:
            if (
                earlier in counts
                and later in counts
                and names.index(earlier) > names.index(later)
            ):
                errors.append(f"{occurrence_id}: {later} before {earlier}")

    for transition in _reload_transitions(events):
        old_revision = transition.get("previous_sample_revision")
        if not old_revision:
            continue
        observed_ns = int(transition["observed_time_unix_ns"])
        task_id = transition.get("task_id")
        bad_admissions = [
            event
            for event in events
            if event.get("event") == "admitted"
            and event.get("task_id") == task_id
            and event.get("sample_revision") == old_revision
            and _time_ns(event) >= observed_ns
        ]
        if bad_admissions:
            errors.append(
                f"{task_id}: {len(bad_admissions)} old-revision admission(s) after hot reload"
            )
        queued_trains = [
            event
            for event in events
            if event.get("event") == "trained"
            and event.get("task_id") == task_id
            and event.get("sample_revision") == old_revision
            and _time_ns(event) >= observed_ns
        ]
        if queued_trains:
            warnings.append(
                f"{task_id}: {len(queued_trains)} already-admitted old-revision group(s) "
                "trained after hot reload"
            )

    return {
        "ok": not errors,
        "occurrences_checked": len(occurrences),
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument(
        "--evolution-events",
        type=Path,
        default=Path(DEFAULT_EVOLUTION_EVENTS) if DEFAULT_EVOLUTION_EVENTS else None,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary")
    step = commands.add_parser("step")
    step.add_argument("train_step", type=int)
    task = commands.add_parser("task")
    task.add_argument("task_id")
    commands.add_parser("audit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    events, event_errors = load_jsonl(args.events)
    evolution_events, evolution_errors = load_jsonl(args.evolution_events)
    if args.command == "summary":
        result = summary_view(events, evolution_events)
    elif args.command == "step":
        result = step_view(events, args.train_step)
    elif args.command == "task":
        result = task_view(events, evolution_events, args.task_id)
    else:
        result = audit_view(events, [*event_errors, *evolution_errors])
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 1 if args.command == "audit" and not result["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
