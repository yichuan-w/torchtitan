from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from mast_rl.mast_job_lifecycle import (
    MastStatusError,
    OPTIMIZATION_COMPLETE,
    optimization_is_complete,
    parse_mast_status,
    stop_evolution_unit,
    training_is_active,
    watch_job,
    write_job_name_file,
)


def _response(job_name: str, state: str) -> str:
    return json.dumps(
        {"data": {"hpcJobName": job_name, "state": state}, "status": "ok"}
    )


def test_write_job_name_file_is_atomic_and_private(tmp_path: Path) -> None:
    output = tmp_path / "state" / "mast_job_name"

    job_name = write_job_name_file(
        output, "mast_conda:///q35-9b-tmax-evolving-v6-abc123"
    )

    assert job_name == "q35-9b-tmax-evolving-v6-abc123"
    assert output.read_text() == f"{job_name}\n"
    assert output.stat().st_mode & 0o777 == 0o600


def test_write_job_name_file_requires_absolute_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        write_job_name_file("relative/job", "mast_conda:///valid-job")


def test_parse_mast_status_requires_the_exact_job() -> None:
    with pytest.raises(MastStatusError, match="expected 'wanted-job'"):
        parse_mast_status(_response("different-job", "RUNNING"), "wanted-job")


def test_stop_evolution_unit_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter(
        [
            subprocess.CompletedProcess([], 5, "", "unit not loaded"),
            subprocess.CompletedProcess([], 3, "", "inactive"),
        ]
    )
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return next(results)

    monkeypatch.setattr(subprocess, "run", run)

    stop_evolution_unit("tmax-evolve-v6")

    assert commands == [
        ["/usr/bin/systemctl", "--user", "stop", "tmax-evolve-v6"],
        [
            "/usr/bin/systemctl",
            "--user",
            "is-active",
            "--quiet",
            "tmax-evolve-v6",
        ],
    ]


def test_optimization_marker_must_match_the_requested_run(tmp_path: Path) -> None:
    marker = tmp_path / "optimization_complete.json"
    marker.write_text(
        json.dumps(
            {
                "event": "optimization_complete",
                "record_type": "optimization_complete",
                "policy_version": 150,
                "requested_training_steps": 150,
            }
        )
    )

    assert optimization_is_complete(marker, expected_training_steps=150)
    assert not optimization_is_complete(marker, expected_training_steps=200)


def test_watch_job_stops_when_optimization_finishes(tmp_path: Path) -> None:
    marker = tmp_path / "optimization_complete.json"
    stopped: list[str] = []
    sleeps: list[float] = []
    queries: list[str] = []
    status_file = tmp_path / "lifecycle.json"

    def query(job_name: str) -> str:
        queries.append(job_name)
        return "RUNNING"

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        marker.write_text(
            json.dumps(
                {
                    "event": "optimization_complete",
                    "record_type": "optimization_complete",
                    "policy_version": 150,
                    "requested_training_steps": 150,
                }
            )
        )

    final_state = watch_job(
        "exact-job",
        "tmax-evolve-v6",
        poll_seconds=17,
        status_file=status_file,
        optimization_marker=marker,
        expected_training_steps=150,
        query_state=query,
        stop_unit=stopped.append,
        sleep=sleep,
    )

    assert final_state == OPTIMIZATION_COMPLETE
    assert queries == ["exact-job"]
    assert stopped == ["tmax-evolve-v6"]
    assert sleeps == [17]
    status = json.loads(status_file.read_text())
    assert status.pop("observed_at").endswith("Z")
    assert status == {
        "evolution_stopped": True,
        "evolution_unit": "tmax-evolve-v6",
        "job_name": "exact-job",
        "state": OPTIMIZATION_COMPLETE,
    }


def test_watch_job_stops_only_after_terminal_state(tmp_path: Path) -> None:
    states = iter(["PENDING", "RUNNING", "SHUTTING_DOWN", "COMPLETE"])
    stopped: list[str] = []
    sleeps: list[float] = []
    status_file = tmp_path / "lifecycle.json"

    final_state = watch_job(
        "exact-job",
        "tmax-evolve-v6",
        poll_seconds=17,
        status_file=status_file,
        query_state=lambda _job_name: next(states),
        stop_unit=stopped.append,
        sleep=sleeps.append,
    )

    assert final_state == "COMPLETE"
    assert stopped == ["tmax-evolve-v6"]
    assert sleeps == [17, 17, 17]
    status = json.loads(status_file.read_text())
    assert status.pop("observed_at").endswith("Z")
    assert status == {
        "evolution_stopped": True,
        "evolution_unit": "tmax-evolve-v6",
        "job_name": "exact-job",
        "state": "COMPLETE",
    }


def test_watch_job_retries_status_errors_without_stopping() -> None:
    calls = 0
    stopped: list[str] = []
    sleeps: list[float] = []

    def query(_job_name: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MastStatusError("temporary query failure")
        return "DEAD"

    final_state = watch_job(
        "exact-job",
        "tmax-evolve-v6",
        poll_seconds=5,
        query_state=query,
        stop_unit=stopped.append,
        sleep=sleeps.append,
    )

    assert final_state == "DEAD"
    assert stopped == ["tmax-evolve-v6"]
    assert sleeps == [5]


def test_repeated_preemptions_pause_admission_and_recover(tmp_path: Path) -> None:
    activity = tmp_path / "activity.json"
    admission = tmp_path / "admission.json"
    activity.write_text(
        json.dumps({"phase": "training", "time_unix_ns": time.time_ns()})
    )
    states = iter(
        ["RUNNING", "FAILED", "PENDING", "RUNNING", "FAILED", "RUNNING", "DEAD"]
    )
    allowed: list[bool] = []
    stopped: list[str] = []

    def sleep(_seconds: float) -> None:
        allowed.append(json.loads(admission.read_text())["allow_admission"])

    final = watch_job(
        "job",
        "unit",
        poll_seconds=5,
        admission_file=admission,
        training_activity=activity,
        query_state=lambda _job: next(states),
        stop_unit=stopped.append,
        sleep=sleep,
    )
    assert final == "DEAD"
    assert allowed == [True, False, False, True, False, True]
    assert stopped == ["unit"]
    assert not json.loads(admission.read_text())["allow_admission"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"phase": "starting", "time_unix_ns": time.time_ns()},
        {"phase": "training", "time_unix_ns": 0},
        {"phase": "training", "time_unix_ns": "invalid"},
    ],
)
def test_inactive_heartbeat_fails_closed(tmp_path: Path, payload: dict) -> None:
    activity = tmp_path / "activity.json"
    assert not training_is_active(activity, max_age_seconds=180)
    activity.write_text(json.dumps(payload))
    assert not training_is_active(activity, max_age_seconds=180)
