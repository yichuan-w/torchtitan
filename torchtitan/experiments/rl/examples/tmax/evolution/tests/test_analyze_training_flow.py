from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_training_flow import audit_view, step_view, summary_view, task_view


def _event(event: str, time_ns: int, occurrence: str, revision: str, **fields):
    return {
        "event": event,
        "time_unix_ns": time_ns,
        "occurrence_id": occurrence,
        "group_id": int(occurrence[-1]),
        "task_id": "task-a",
        "sample_revision": revision,
        "mix_revision": "mix-1",
        "dataset_epoch": 0,
        "dataset_position": int(occurrence[-1]),
        **fields,
    }


def _synthetic_events():
    old = [
        _event("admitted", 10, "occ-1", "old"),
        _event("claimed", 20, "occ-1", "old"),
        _event("finalized", 30, "occ-1", "old", rollout_duration_sec=1.0),
        _event("selected", 40, "occ-1", "old", solve_class="partial_solve"),
        _event("packed", 50, "occ-1", "old", reserved_train_step=8),
    ]
    reload_event = {
        "event": "hot_reload",
        "time_unix_ns": 56,
        "observed_time_unix_ns": 55,
        "mix_revision": "mix-2",
        "changes": [
            {
                "task_id": "task-a",
                "change": "replaced",
                "previous_sample_revision": "old",
                "sample_revision": "new",
            }
        ],
    }
    old.append(_event("trained", 60, "occ-1", "old", train_step=8))
    new = [
        _event("admitted", 70, "occ-2", "new", mix_revision="mix-2"),
        _event("claimed", 80, "occ-2", "new", mix_revision="mix-2"),
        _event(
            "finalized",
            90,
            "occ-2",
            "new",
            mix_revision="mix-2",
            rollout_duration_sec=2.0,
            bypass_count=3,
        ),
        _event(
            "selected",
            100,
            "occ-2",
            "new",
            mix_revision="mix-2",
            solve_class="partial_solve",
        ),
        _event(
            "packed",
            110,
            "occ-2",
            "new",
            mix_revision="mix-2",
            reserved_train_step=9,
        ),
        _event(
            "trained",
            120,
            "occ-2",
            "new",
            mix_revision="mix-2",
            train_step=9,
        ),
    ]
    return [*old[:5], reload_event, old[5], *new]


def test_step_and_task_views_keep_exact_revisions() -> None:
    events = _synthetic_events()
    evolution = [
        {
            "event": "folded",
            "time_unix_ns": 50,
            "task_id": "task-a",
            "sample_revision": "new",
        }
    ]

    step = step_view(events, 9)
    task = task_view(events, evolution, "task-a")

    assert step["groups"] == [
        {
            "occurrence_id": "occ-2",
            "group_id": 2,
            "task_id": "task-a",
            "sample_revision": "new",
            "mix_revision": "mix-2",
            "dataset_epoch": 0,
            "dataset_position": 2,
            "events": ["packed", "trained"],
        }
    ]
    assert "hot_reload_task_change" in [event["event"] for event in task["events"]]


def test_summary_measures_reload_activation_and_queued_old_work() -> None:
    events = _synthetic_events()
    evolution = [
        {
            "event": "retune_finished",
            "time_unix_ns": 45,
            "task_id": "task-a",
            "solved": 0,
            "total": 16,
        },
        {
            "event": "folded",
            "time_unix_ns": 50,
            "task_id": "task-a",
            "sample_revision": "new",
        },
    ]

    summary = summary_view(events, evolution)
    activation = summary["evolution_activation"][0]

    assert activation["old_revision_groups_trained_after_reload"] == 1
    assert activation["reload_to_first_admitted_sec"] == 15e-9
    assert activation["reload_to_first_trained_sec"] == 65e-9
    assert activation["fold_to_reload_sec"] == 5e-9
    assert summary["evolution_signal_outcomes"] == {"all_failed": 1}


def test_audit_distinguishes_queued_old_work_from_bad_post_reload_admission() -> None:
    events = _synthetic_events()

    audit = audit_view(events, [])
    assert audit["ok"]
    assert "already-admitted old-revision" in audit["warnings"][0]

    events.append(_event("admitted", 130, "occ-3", "old"))
    bad = audit_view(events, [])
    assert not bad["ok"]
    assert "old-revision admission" in bad["errors"][0]


def test_cli_reads_training_and_evolution_jsonl_end_to_end(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    evolution_path = tmp_path / "evolution.jsonl"
    events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in _synthetic_events())
    )
    evolution_path.write_text(
        json.dumps(
            {
                "event": "folded",
                "time_unix_ns": 50,
                "task_id": "task-a",
                "sample_revision": "new",
            }
        )
        + "\n"
    )
    script = Path(__file__).resolve().parents[1] / "analyze_training_flow.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--events",
            str(events_path),
            "--evolution-events",
            str(evolution_path),
            "step",
            "9",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)

    assert output["num_groups"] == 1
    assert output["groups"][0]["task_id"] == "task-a"
    assert output["groups"][0]["sample_revision"] == "new"
