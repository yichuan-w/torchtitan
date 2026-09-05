"""analyze_training_flow against a root in the LAYOUT.md shape: the
controller's lifecycle events under the run's trainer/, the loop's lineage,
rewrite and ledger records under evolution/, all placed with layout.py so the
join is tested on the real file names."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[2]))

import layout  # noqa: E402
from analyze_training_flow import (  # noqa: E402
    audit_view,
    controller_events,
    load_jsonl,
    load_lineage,
    step_view,
    summary_view,
    task_view,
)

RUN = "tmax-9b--20260904-100000Z"
FOLD_STAMP = "20260904-110000Z"
FOLD_NS = int(layout.parse_stamp(FOLD_STAMP) * 10**9)
REWRITE = "rewrites/20260904-103000Z--harder"


def _event(event: str, offset_ns: int, occurrence: str, revision: str, **fields):
    """A controller event ``offset_ns`` after the fold, so the two clocks meet."""
    return {
        "event": event,
        "time_unix_ns": FOLD_NS + offset_ns,
        "occurrence_id": occurrence,
        "group_id": int(occurrence[-1]),
        "task_id": "task-a",
        "sample_revision": revision,
        "mix_revision": "mix-1",
        "dataset_epoch": 0,
        "dataset_position": int(occurrence[-1]),
        **fields,
    }


def _controller_events():
    # An old-revision group admitted before the reload and trained after it
    # is queued stale work, not a broken invariant; the new revision follows.
    old = [
        _event("admitted", 10, "occ-1", "old"),
        _event("claimed", 20, "occ-1", "old"),
        _event("finalized", 30, "occ-1", "old", rollout_duration_sec=1.0),
        _event("selected", 40, "occ-1", "old", solve_class="partial_solve"),
        _event("packed", 50, "occ-1", "old", reserved_train_step=8),
    ]
    reload_event = {
        "event": "hot_reload",
        "time_unix_ns": FOLD_NS + 56,
        "observed_time_unix_ns": FOLD_NS + 55,
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
        _event("finalized", 90, "occ-2", "new", mix_revision="mix-2",
               rollout_duration_sec=2.0, bypass_count=3),
        _event("selected", 100, "occ-2", "new", mix_revision="mix-2", solve_class="partial_solve"),
        _event("packed", 110, "occ-2", "new", mix_revision="mix-2", reserved_train_step=9),
        _event("trained", 120, "occ-2", "new", mix_revision="mix-2", train_step=9),
    ]
    return [*old[:5], reload_event, old[5], *new]


def _root(tmp_path: Path) -> layout.Root:
    root = layout.Root(tmp_path / "exp-dev")
    run = root.run(RUN)
    layout.write_json_atomic(run.launch_json, {"run": RUN, "started": "20260904-100000Z"})
    events = controller_events(run)
    events.parent.mkdir(parents=True)
    events.write_text("".join(json.dumps(e) + "\n" for e in _controller_events()))
    root.latest.symlink_to(RUN)

    evo = root.evolution
    signal = layout.signal_id(RUN, "task-a", 7)
    layout.append_jsonl(evo.ledger, {
        "stamp": "20260904-103000Z", "signal": signal, "task": "task-a", "rev": 0,
        "run": RUN, "group": 7, "direction": "harder", "outcome": "handled",
        "rewrite": f"tasks/task-a/{REWRITE}"})
    layout.append_jsonl(evo.ledger, {
        "stamp": "20260904-103100Z", "signal": layout.signal_id(RUN, "task-b", 8),
        "task": "task-b", "rev": 0, "run": RUN, "group": 8, "direction": "easier",
        "outcome": "deferred", "rewrite": None})
    task = evo.task("task-a")
    layout.write_json_atomic(task.rewrite("harder", "20260904-103000Z").meta, {
        "task": "task-a", "job": "harder", "signal": signal, "input_rev": 0,
        "started": "20260904-103000Z", "finished": "20260904-104500Z", "status": "accepted",
        "operator": "container_build_alignment", "arm": "codex",
        "verdicts": {"oracle": "pass", "dark_paths": [], "dark_literals": [], "step": []},
        "result_rev": 1, "sessions": []})
    layout.append_jsonl(task.lineage, {
        "stamp": "20260904-104500Z", "event": "rewrite", "rewrite": REWRITE,
        "job": "harder", "input_rev": 0, "status": "accepted"})
    layout.append_jsonl(task.lineage, {
        "stamp": FOLD_STAMP, "event": "fold", "from_rev": 0, "to_rev": 1,
        "mix_version": 2, "rewrite": REWRITE})
    return root


def _load(root: layout.Root):
    events, errors = load_jsonl(controller_events(layout.Run(root.latest.resolve())))
    assert not errors
    return events, load_lineage(root.evolution), layout.read_jsonl(root.evolution.ledger)


def test_step_and_task_views_keep_exact_revisions(tmp_path) -> None:
    events, lineage, _ = _load(_root(tmp_path))

    step = step_view(events, 9)
    task = task_view(events, lineage, "task-a")

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
    names = [event["event"] for event in task["events"]]
    assert "hot_reload_task_change" in names
    # One clock: the rewrite, then its fold, then everything the trainer did after.
    assert names.index("rewrite") < names.index("fold") < names.index("admitted")
    rewrite = next(event for event in task["events"] if event["event"] == "rewrite")
    assert rewrite["operator"] == "container_build_alignment"  # from rewrite.json, not the lineage
    assert (rewrite["arm"], rewrite["result_rev"]) == ("codex", 1)
    fold = next(event for event in task["events"] if event["event"] == "fold")
    assert (fold["mix_version"], fold["time_unix_ns"]) == (2, FOLD_NS)


def test_summary_measures_reload_activation_and_queued_old_work(tmp_path) -> None:
    events, lineage, ledger = _load(_root(tmp_path))

    summary = summary_view(events, lineage, ledger)
    activation = summary["evolution_activation"][0]

    assert activation["old_revision_groups_trained_after_reload"] == 1
    assert activation["reload_to_first_admitted_sec"] == 15e-9
    assert activation["reload_to_first_trained_sec"] == 65e-9
    assert activation["fold_to_reload_sec"] == 55e-9
    assert (activation["fold_to_rev"], activation["fold_mix_version"]) == (1, 2)
    assert summary["signal_outcomes"] == {"easier": {"deferred": 1}, "harder": {"handled": 1}}
    assert summary["rewrite_statuses"] == {"accepted": 1}
    counts = summary["counts"]
    assert (counts["rewrites"], counts["folds"], counts["signals"], counts["hot_reloads"]) == (1, 1, 2, 1)


def test_audit_distinguishes_queued_old_work_from_bad_post_reload_admission(tmp_path) -> None:
    events, _, _ = _load(_root(tmp_path))

    audit = audit_view(events, [])
    assert audit["ok"]
    assert "already-admitted old-revision" in audit["warnings"][0]

    events.append(_event("admitted", 130, "occ-3", "old"))
    bad = audit_view(events, [])
    assert not bad["ok"]
    assert "old-revision admission" in bad["errors"][0]


def test_cli_reads_the_run_and_the_loop_end_to_end(tmp_path) -> None:
    root = _root(tmp_path)
    script = HERE.parents[1] / "analyze_training_flow.py"

    result = subprocess.run(
        [sys.executable, str(script), "--root", str(root.path), "step", "9"],
        check=True, capture_output=True, text=True,
    )
    output = json.loads(result.stdout)
    assert output["num_groups"] == 1
    assert output["groups"][0]["task_id"] == "task-a"
    assert output["groups"][0]["sample_revision"] == "new"

    # --run names the run outright; without it runs/latest is the one read.
    named = subprocess.run(
        [sys.executable, str(script), "--root", str(root.path), "--run", RUN, "task", "task-a"],
        check=True, capture_output=True, text=True,
    )
    assert "hot_reload_task_change" in named.stdout
