from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "della"))

import publish_evolve_analysis as analysis


def _round() -> dict:
    return {
        "round": 1,
        "timestamp": "2026-08-30T02:59:48+00:00",
        "elapsed_hours": 1.5,
        "commit": "abc123",
        "processed": 3,
        "retuned": 1,
        "folded": 1,
        "folded_cumulative": 1,
        "accepted_harder": 1,
        "kept": 0,
        "revalidate_failed": 2,
        "deferred_easier": 0,
        "no_pool_dir": 0,
        "unaccounted": 0,
        "counts": {"ok": 1, "revalidate_failed": 2},
        "changed_task_ids": ["task-a"],
        "task_changes": [
            {
                "task_id": "task-a",
                "source_sample_revision": "old-sha",
                "folded_sample_revision": "new-sha",
            }
        ],
    }


def test_evolution_trace_has_round_and_fold_records() -> None:
    records = analysis.evolution_trace_rows([_round()])

    assert [record["record_type"] for record in records] == [
        "evolution_round",
        "evolution_fold",
    ]
    assert records[1]["task_id"] == "task-a"
    assert records[1]["source_sample_revision"] == "old-sha"
    assert records[1]["folded_sample_revision"] == "new-sha"


def test_write_evolution_trace_is_stable_and_path_free(tmp_path) -> None:
    path = tmp_path / "evolution_trace.jsonl"

    analysis.write_evolution_trace(path, [_round()])
    first = path.read_text()
    analysis.write_evolution_trace(path, [_round()])

    records = [json.loads(line) for line in first.splitlines()]
    assert path.read_text() == first
    assert len(records) == 2
    assert "/scratch/" not in first
