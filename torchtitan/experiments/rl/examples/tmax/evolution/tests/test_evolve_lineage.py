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

    def fake_handle(path, declared=None):
        seen.append(path.name)
        return {"tid": "task-a"}

    monkeypatch.setattr(od, "_handle", fake_handle)

    od._handle_task_signals([later, earlier])

    assert seen == [earlier.name, later.name]


def test_handle_passes_the_training_box_to_process_one(tmp_path, monkeypatch) -> None:
    signal = tmp_path / "task-a.json"
    signal.write_text(json.dumps({"task_id": "task-a", "solved": 16, "total": 16,
                                  "attempts": [{"turns": 3}]}))
    seen = {}

    def fake_process_one(rollout, src, out_root, resources=None):
        seen["resources"] = resources
        return {"status": "ok", "action": "evolve", "solved": 16, "graded": 16,
                "resources": {"cpu": 2, "mem_gb": 2, "disk_gb": 2, "source": "measured"}}

    monkeypatch.setattr(od, "resolve_src", lambda _task_id: tmp_path)
    monkeypatch.setattr(od.fb, "process_one", fake_process_one)
    monkeypatch.setattr(od, "OUT_ROOT", tmp_path / "retuned")
    monkeypatch.setattr(od, "FLEET", {"cpu": None, "mem_gb": None, "disk_gb": None})

    result = od._handle(signal, {"task-a": {"cpu": 1, "mem_gb": 2, "disk_gb": 2}})
    assert seen["resources"] == {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}
    assert result["resources"]["source"] == "measured"

    # A row declaring nothing, with no fleet default in the env: the harness
    # default applies, and the source says so rather than inventing a number.
    od._handle(signal, {})
    assert seen["resources"] == {"cpu": None, "mem_gb": None, "disk_gb": None,
                                 "source": "harness_default"}

    monkeypatch.setattr(od, "FLEET", {"cpu": 1, "mem_gb": 2, "disk_gb": 2})
    od._handle(signal, {"task-a": {"mem_gb": 4}})
    assert seen["resources"] == {"cpu": 1, "mem_gb": 4, "disk_gb": 2,
                                 "source": "row+fleet_default"}


def _fold_fixture(tmp_path, monkeypatch, provision: dict | None) -> Path:
    """One k/k signal for task-a, a mix row at 1/2/2, and a retuned package
    that may carry the size feedback_loop measured."""
    signals = tmp_path / "signals"
    signals.mkdir()
    (signals / "task-a.json").write_text(json.dumps(
        {"task_id": "task-a", "solved": 16, "total": 16, "attempts": [{"turns": 3}]}))
    mix = tmp_path / "mix_live.jsonl"
    mix.write_text(json.dumps({"label": "task-a", "metadata": {
        "instance_id": "task-a", "daytona_cpu": 1, "daytona_mem_gb": 2,
        "daytona_disk_gb": 2}}) + "\n")
    retuned = tmp_path / "retuned" / "task-a"
    retuned.mkdir(parents=True)
    if provision is not None:
        (retuned / ".resources.json").write_text(json.dumps(provision))
    monkeypatch.setattr(od, "SIGNALS", signals)
    monkeypatch.setattr(od, "CONSUMED", tmp_path / "consumed")
    monkeypatch.setattr(od, "OUT_ROOT", tmp_path / "retuned")
    monkeypatch.setattr(od, "MIX", mix)
    monkeypatch.setattr(od, "LINEAGE", tmp_path / "lineage.jsonl")
    monkeypatch.setattr(od, "_handle", lambda _sp, declared=None: {
        "tid": "task-a", "status": "ok", "retuned": True, "action": "evolve",
        "solved": 16, "graded": 16})
    # pack.to_row has no source for daytona_*: the folded row arrives without them.
    monkeypatch.setattr(od.pack, "to_row", lambda _d: {
        "label": "task-a", "metadata": {"instance_id": "task-a"}})
    return tmp_path / "out.jsonl"


def test_fold_provisions_the_row_from_the_measured_size(tmp_path, monkeypatch) -> None:
    out = _fold_fixture(tmp_path, monkeypatch, {
        "cpu": 2, "mem_gb": 4, "disk_gb": 2, "source": "measured",
        "floor": {"cpu": 1, "mem_gb": 2, "disk_gb": 2},
        "sized": {"cpu": 2, "mem_gb": 4, "disk_gb": 2}})

    r = od.run_round(mix_out=out, keep_signal=True)

    assert r["folded"] == 1
    md = json.loads(out.read_text())["metadata"]
    assert (md["daytona_cpu"], md["daytona_mem_gb"], md["daytona_disk_gb"]) == (2, 4, 2)
    events = [json.loads(l) for l in (tmp_path / "lineage.jsonl").read_text().splitlines()]
    folded = [e for e in events if e["event"] == "folded"][0]
    assert folded["resources"] == {"daytona_cpu": 2, "daytona_mem_gb": 4,
                                   "daytona_disk_gb": 2, "source": "measured"}


def test_fold_inherits_the_seed_size_without_a_measurement(tmp_path, monkeypatch) -> None:
    out = _fold_fixture(tmp_path, monkeypatch, None)

    od.run_round(mix_out=out, keep_signal=True)

    md = json.loads(out.read_text())["metadata"]
    assert (md["daytona_cpu"], md["daytona_mem_gb"], md["daytona_disk_gb"]) == (1, 2, 2)
    events = [json.loads(l) for l in (tmp_path / "lineage.jsonl").read_text().splitlines()]
    folded = [e for e in events if e["event"] == "folded"][0]
    assert folded["resources"]["source"] == "inherited"
