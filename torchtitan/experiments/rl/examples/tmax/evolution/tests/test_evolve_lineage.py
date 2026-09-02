from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od


def test_handle_preserves_source_training_occurrence(tmp_path, monkeypatch) -> None:
    signal = tmp_path / "task-a.json"
    signal.write_text(
        json.dumps(
            {
                "task_id": "task-a",
                "solved": 0,
                "total": 16,
                "attempts": [{"turns": 1}],
                "created_time_unix_ns": 123,
                "source_group_id": 9,
                "source_lineage": {
                    "occurrence_id": "stream:12",
                    "sample_revision": "old-revision",
                    "mix_revision": "old-mix",
                },
            }
        )
    )
    monkeypatch.setattr(od, "resolve_src", lambda _task_id: tmp_path)
    monkeypatch.setattr(
        od.fb,
        "process_one",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "action": "simplify",
            "solved": 0,
            "graded": 16,
        },
    )
    monkeypatch.setattr(od, "OUT_ROOT", tmp_path / "retuned")

    result = od._handle(signal)

    assert result["source_group_id"] == 9
    assert result["source_lineage"] == {
        "occurrence_id": "stream:12",
        "sample_revision": "old-revision",
        "mix_revision": "old-mix",
    }


def test_append_lineage_event_is_append_only(tmp_path) -> None:
    path = tmp_path / "evolution_lineage.jsonl"

    od.append_lineage_event("retune_finished", path=path, task_id="task-a")
    od.append_lineage_event("folded", path=path, task_id="task-a")

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "retune_finished",
        "folded",
    ]
    assert all(record["time_unix_ns"] > 0 for record in records)


def test_codex_trace_references_are_relative_to_evolution_root(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "evolution"
    monkeypatch.setattr(od, "EVOLUTION_ROOT", root)

    assert od._trace_references(
        [str(root / "signals/codex_traces/codex-harder-abc")]
    ) == ["signals/codex_traces/codex-harder-abc"]


def test_lineage_snapshot_does_not_commit_codex_traces(tmp_path, monkeypatch) -> None:
    root = tmp_path / "evolution"
    trace = root / "signals/codex_traces/codex-harder-abc/trace.json"
    trace.parent.mkdir(parents=True)
    trace.write_text("{}\n")
    mix = tmp_path / "mix.jsonl"
    mix.write_text('{"metadata":{"instance_id":"task-a"}}\n')
    monkeypatch.setattr(od, "EVOLUTION_ROOT", root)
    monkeypatch.setattr(od, "MIX", mix)

    od._snapshot_lineage("test snapshot")

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert "signals/codex_traces/codex-harder-abc/trace.json" not in tracked
    assert "signals/" in (root / ".gitignore").read_text().splitlines()


def test_repeated_task_signals_run_in_creation_order(tmp_path, monkeypatch) -> None:
    later = tmp_path / "task-a--later.json"
    earlier = tmp_path / "task-a--earlier.json"
    later.write_text(json.dumps({"created_time_unix_ns": 20}))
    earlier.write_text(json.dumps({"created_time_unix_ns": 10}))
    seen = []

    def fake_handle(path):
        seen.append(path.name)
        return {"tid": "task-a"}

    monkeypatch.setattr(od, "_handle", fake_handle)

    od._handle_task_signals([later, earlier])

    assert seen == [earlier.name, later.name]
