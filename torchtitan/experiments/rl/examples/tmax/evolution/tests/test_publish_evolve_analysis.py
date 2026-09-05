"""publish_evolve_analysis against a root in the LAYOUT.md shape, built with
layout.py and rollout_record.py rather than by hand, so a change to either
contract fails here before it fails on the training host."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "della"))
sys.path.insert(0, str(HERE.parents[2]))

import layout  # noqa: E402
import publish_evolve_analysis as analysis  # noqa: E402
import rollout_record  # noqa: E402

RUN = "tmax-9b--20260904-100000Z"
REWRITE = "rewrites/20260904-103000Z--harder"
_t = layout.parse_stamp


def _row(task: str, rev: int) -> str:
    return json.dumps({"prompt": "", "label": task,
                       "metadata": {"instance_id": task, "rev": rev}})


def _root(tmp_path: Path) -> layout.Root:
    root = layout.Root(tmp_path / "exp-dev")
    layout.write_json_atomic(root.experiment_json, {
        "name": "exp-dev", "created": "20260904-090000Z", "profile": "andy",
        "purpose": "test", "seed_mix_version": 1, "forked_from": None})
    # v1 is the seed; v2 carries task-a at rev 1 after the fold below.
    root.mix.publish([_row("task-a", 0), _row("task-b", 0)], t=_t("20260904-090000Z"))
    root.mix.publish([_row("task-a", 1), _row("task-b", 0)], t=_t("20260904-110000Z"))

    run = root.run(RUN)
    layout.write_json_atomic(run.launch_json, {
        "run": RUN, "started": "20260904-100000Z", "profile": "andy", "tt_commit": "abc123",
        "mix_version": 1, "mix_sha256": "x", "gpus": "0", "resumed_from": None,
        "checkpoint_step": None, "env": {}})
    for idx in range(4):
        rollout_record.write_record(
            run.rollout_record("task-a", 7, idx),
            {"task": "task-a", "rev": 0, "run": RUN, "group": 7, "rollout": idx,
             "reward": 1.0, "status": "completed", "finish_reason": "submit",
             "submitted": True, "format_errors": 0, "infra_failed": False, "error": "",
             "secs": 10.0, "budget_sec": 1800, "turns": 3, "started": "20260904-101000Z",
             "exec": []},
            [{"turn": 1, "keystrokes": ["ls\n"], "output": ""}])
    signal = layout.signal_id(RUN, "task-a", 7)
    layout.write_json_atomic(run.signal("task-a", 7), {
        "task": "task-a", "rev": 0, "run": RUN, "group": 7, "direction": "harder",
        "solved": 4, "total": 4, "created": "20260904-102000Z",
        "attempts": [f"rollouts/task-a/g7-r{i}.jsonl" for i in range(4)]})

    evo = root.evolution
    layout.append_jsonl(evo.ledger, {
        "stamp": "20260904-103000Z", "signal": signal, "task": "task-a", "rev": 0,
        "run": RUN, "group": 7, "direction": "harder", "outcome": "handled",
        "rewrite": f"tasks/task-a/{REWRITE}"})
    layout.append_jsonl(evo.ledger, {
        "stamp": "20260904-103100Z", "signal": layout.signal_id(RUN, "task-b", 8),
        "task": "task-b", "rev": 0, "run": RUN, "group": 8, "direction": "easier",
        "outcome": "deferred", "rewrite": None})
    task = evo.task("task-a")
    rw = task.rewrite("harder", "20260904-103000Z")
    layout.write_json_atomic(rw.meta, {
        "task": "task-a", "job": "harder", "signal": signal, "input_rev": 0,
        "started": "20260904-103000Z", "finished": "20260904-104500Z", "status": "accepted",
        "operator": "container_build_alignment", "arm": "codex",
        "verdicts": {"oracle": "pass", "dark_paths": [], "dark_literals": [], "step": []},
        "resources": {"cpu": 2, "mem_gb": 4, "disk_gb": 6, "source": "measured"},
        "result_rev": 1, "sessions": ["sessions/20260904-103001Z--agent"]})
    layout.append_jsonl(task.lineage, {
        "stamp": "20260904-104500Z", "event": "rewrite", "rewrite": REWRITE,
        "job": "harder", "input_rev": 0, "status": "accepted"})
    layout.append_jsonl(task.lineage, {
        "stamp": "20260904-110000Z", "event": "fold", "from_rev": 0, "to_rev": 1,
        "mix_version": 2, "rewrite": REWRITE})
    return root


def test_rounds_are_mix_versions_with_their_folds(tmp_path) -> None:
    root = _root(tmp_path)

    rounds, summary = analysis.load_evolution_metrics(
        root, _t("20260904-100000Z"), _t("20260904-120000Z"))

    assert [r["version"] for r in rounds] == [2]  # the seed version is nobody's round
    r = rounds[0]
    assert r["elapsed_hours"] == 1.0
    assert r["folded"] == 1 and r["folded_cumulative"] == 1
    assert r["changed_task_ids"] == ["task-a"]
    assert r["task_changes"] == [
        {"task": "task-a", "from_rev": 0, "to_rev": 1, "rewrite": REWRITE}]
    assert r["rewrites"] == {"accepted": 1, "rejected": 0, "blocked": 0, "failed": 0, "kept": 0}
    assert r["harder_attempted"] == 1 and r["harder_accept_rate"] == 1.0
    assert r["signals"] == {"handled": 1, "deferred": 1, "junk": 0}
    assert r["signals_by_direction"] == {"easier": {"deferred": 1}, "harder": {"handled": 1}}
    assert summary["rounds"] == 1 and summary["folded"] == 1
    assert summary["unique_changed_tasks"] == 1 and summary["repeat_folds"] == 0


def test_lineage_and_mix_history_must_agree(tmp_path) -> None:
    root = _root(tmp_path)
    layout.append_jsonl(root.evolution.task("task-b").lineage, {
        "stamp": "20260904-110000Z", "event": "fold", "from_rev": 0, "to_rev": 1,
        "mix_version": 2, "rewrite": "rewrites/20260904-105000Z--harder"})

    with pytest.raises(RuntimeError, match="mix v2"):
        analysis.load_evolution_metrics(root, _t("20260904-100000Z"), _t("20260904-120000Z"))


def test_evolution_trace_has_round_and_fold_records(tmp_path) -> None:
    root = _root(tmp_path)
    rounds, _ = analysis.load_evolution_metrics(
        root, _t("20260904-100000Z"), _t("20260904-120000Z"))

    records = analysis.evolution_trace_rows(rounds)

    assert [record["record_type"] for record in records] == [
        "evolution_round",
        "evolution_fold",
    ]
    assert records[1]["task"] == "task-a"
    assert (records[1]["from_rev"], records[1]["to_rev"]) == (0, 1)
    assert records[1]["rewrite"] == REWRITE


def test_write_evolution_trace_is_stable_and_path_free(tmp_path) -> None:
    root = _root(tmp_path)
    rounds, _ = analysis.load_evolution_metrics(
        root, _t("20260904-100000Z"), _t("20260904-120000Z"))
    path = tmp_path / "evolution_trace.jsonl"

    analysis.write_evolution_trace(path, rounds)
    first = path.read_text()
    analysis.write_evolution_trace(path, rounds)

    records = [json.loads(line) for line in first.splitlines()]
    assert path.read_text() == first
    assert len(records) == 2
    assert str(tmp_path) not in first


def test_rollout_audit_sees_whole_groups(tmp_path) -> None:
    run = _root(tmp_path).run(RUN)

    whole = analysis.audit_rollouts(run, 4)
    assert (whole["records"], whole["groups"], whole["complete_groups"]) == (4, 1, 1)
    assert whole["population_complete"] and whole["status_counts"] == {"completed": 4}

    short = analysis.audit_rollouts(run, 16)
    assert short["partial_groups"] == 1 and not short["population_complete"]
    assert short["records_sha256"] == whole["records_sha256"]
