from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import finalize_interrupted_traces as fit


def _write_trace(root: Path, name: str, *, status: str, started: int) -> Path:
    path = root / name / "trace.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "status": status,
                "task_id": name,
                "started_time_unix_ns": started,
                "finished_time_unix_ns": None,
            }
        )
        + "\n"
    )
    path.chmod(0o600)
    return path


def test_finalize_backs_up_and_marks_only_running_traces(tmp_path) -> None:
    running = _write_trace(tmp_path, "codex-harder-a", status="running", started=10)
    completed = _write_trace(
        tmp_path, "codex-harder-b", status="completed", started=20
    )
    original = running.read_text()

    counts = fit.finalize_interrupted_traces(tmp_path, stopped_loop_pid=123)

    record = json.loads(running.read_text())
    assert counts == {"marked": 1, "skipped": 1, "failed": 0}
    assert record["status"] == "interrupted"
    assert record["finished_time_source"] == "restart_observation"
    assert record["stopped_loop_pid"] == 123
    assert running.with_name("trace.pre-finalize.json").read_text() == original
    assert stat.S_IMODE(running.stat().st_mode) == 0o600
    assert json.loads(completed.read_text())["status"] == "completed"
    assert not completed.with_name("trace.pre-finalize.json").exists()

    assert fit.finalize_interrupted_traces(tmp_path, stopped_loop_pid=123) == {
        "marked": 0,
        "skipped": 2,
        "failed": 0,
    }


def test_finalize_respects_started_before_cutoff(tmp_path) -> None:
    older = _write_trace(tmp_path, "codex-harder-old", status="running", started=10)
    newer = _write_trace(tmp_path, "codex-harder-new", status="running", started=30)

    counts = fit.finalize_interrupted_traces(
        tmp_path, stopped_loop_pid=456, started_before_unix_ns=20
    )

    assert counts == {"marked": 1, "skipped": 1, "failed": 0}
    assert json.loads(older.read_text())["status"] == "interrupted"
    assert json.loads(newer.read_text())["status"] == "running"
