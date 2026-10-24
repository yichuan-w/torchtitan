"""Stop TMax evolution when optimization finishes or its MAST job terminates."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ACTIVE_STATES = frozenset({"PENDING", "RUNNING", "SHUTTING_DOWN", "FAILED"})
# MAST can report FAILED while the application retry policy starts a new attempt.
TERMINAL_STATES = frozenset({"COMPLETE", "DEAD"})
KNOWN_STATES = ACTIVE_STATES | TERMINAL_STATES
JOB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
UNIT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]+$")
OPTIMIZATION_COMPLETE = "OPTIMIZATION_COMPLETE"


class MastStatusError(RuntimeError):
    """A MAST response could not establish the exact job's state."""


def job_name_from_handle(handle: str) -> str:
    """Return and validate the job name from a MAST application handle."""
    job_name = handle.rsplit("/", 1)[-1]
    if not job_name or not JOB_NAME_PATTERN.fullmatch(job_name):
        raise ValueError(f"invalid MAST job handle: {handle!r}")
    return job_name


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", text=True
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_job_name_file(path: str | Path, handle: str) -> str:
    """Atomically record the submitted MAST job for a local lifecycle watcher."""
    output_path = Path(path)
    if not output_path.is_absolute():
        raise ValueError(f"MAST job-name output path must be absolute: {output_path}")
    job_name = job_name_from_handle(handle)
    _atomic_write_text(output_path, f"{job_name}\n")
    return job_name


def parse_mast_status(payload: str, expected_job_name: str) -> str:
    """Validate a JSON status response and return its top-level MAST state."""
    try:
        response: Any = json.loads(payload)
        if "data" in response:
            data = response["data"]
            response_status = response["status"]
        else:  # `mslsc get-status --json` returns the job object itself
            data = response
            response_status = "ok"
        job_name = data["hpcJobName"]
        state = data["state"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise MastStatusError(f"invalid MAST status response: {error}") from error

    if response_status != "ok":
        raise MastStatusError(f"MAST status request returned {response_status!r}")
    if job_name != expected_job_name:
        raise MastStatusError(
            f"MAST returned job {job_name!r}, expected {expected_job_name!r}"
        )
    if state not in KNOWN_STATES:
        raise MastStatusError(f"MAST returned unknown state {state!r}")
    return state


MSLSC_BINARY = "/usr/local/bin/mslsc"


def query_mast_state(
    job_name: str,
    *,
    mast_binary: str = "/usr/local/bin/mast",
    timeout_seconds: int = 30,
) -> str:
    """Query the authoritative top-level state for one exact MAST job."""
    try:
        result = subprocess.run(
            [mast_binary, "--output", "json", "get-status", job_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MastStatusError(f"MAST status query failed: {error}") from error
    if any(
        marker in result.stdout + result.stderr
        for marker in ("MSL Scheduler", "JOB_NOT_FOUND")
    ):
        # `mast` no longer answers for jobs the MSL Scheduler owns (it exits 0
        # with an error object, now JOB_NOT_FOUND), so ask `mslsc`.
        try:
            result = subprocess.run(
                [MSLSC_BINARY, "get-status", "--json", job_name],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MastStatusError(f"mslsc status query failed: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise MastStatusError(f"MAST status query exited {result.returncode}: {detail}")
    return parse_mast_status(result.stdout, job_name)


def stop_evolution_unit(
    unit_name: str, *, systemctl_binary: str = "/usr/bin/systemctl"
) -> None:
    """Stop the evolution unit without affecting its independent W&B service."""
    if not UNIT_NAME_PATTERN.fullmatch(unit_name):
        raise ValueError(f"invalid systemd unit name: {unit_name!r}")
    result = subprocess.run(
        [systemctl_binary, "--user", "stop", unit_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        active = subprocess.run(
            [systemctl_binary, "--user", "is-active", "--quiet", unit_name],
            check=False,
            capture_output=True,
            text=True,
        )
        if active.returncode != 0:
            return
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RuntimeError(f"failed to stop evolution unit {unit_name}: {detail}")


def optimization_is_complete(
    marker_path: Path, *, expected_training_steps: int
) -> bool:
    """Return whether an atomic optimization marker matches this launch."""
    try:
        marker: Any = json.loads(marker_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(marker, dict)
        and marker.get("event") == "optimization_complete"
        and marker.get("record_type") == "optimization_complete"
        and marker.get("requested_training_steps") == expected_training_steps
        and isinstance(marker.get("policy_version"), int)
    )


def _write_lifecycle_status(
    status_file: Path | None,
    *,
    job_name: str,
    evolution_unit: str,
    state: str,
    evolution_stopped: bool,
) -> None:
    if status_file is None:
        return
    payload = {
        "evolution_stopped": evolution_stopped,
        "evolution_unit": evolution_unit,
        "job_name": job_name,
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "state": state,
    }
    _atomic_write_text(status_file, json.dumps(payload, sort_keys=True) + "\n")


def write_admission_status(
    path: Path,
    *,
    allow_admission: bool,
    reason: str,
    lease_seconds: int = 180,
) -> None:
    """Publish an expiring lease; a missing watcher must fail closed."""
    _atomic_write_text(
        path,
        json.dumps(
            {
                "allow_admission": allow_admission,
                "reason": reason,
                "expires_at": time.time() + lease_seconds,
            },
            sort_keys=True,
        )
        + "\n",
    )


def training_is_active(path: Path, *, max_age_seconds: int) -> bool:
    """Require recent controller progress, not merely a RUNNING allocation."""
    try:
        payload = json.loads(path.read_text())
        age = time.time() - payload["time_unix_ns"] / 1e9
        return payload["phase"] == "training" and 0 <= age < max_age_seconds
    except (OSError, KeyError, TypeError, ValueError):
        return False


def watch_job(
    job_name: str,
    evolution_unit: str,
    *,
    poll_seconds: int,
    status_file: Path | None = None,
    optimization_marker: Path | None = None,
    expected_training_steps: int | None = None,
    admission_file: Path | None = None,
    training_activity: Path | None = None,
    heartbeat_max_age_seconds: int = 180,
    query_state: Callable[[str], str] = query_mast_state,
    stop_unit: Callable[[str], None] = stop_evolution_unit,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Stop evolution after optimization completes or MAST becomes terminal."""
    if not JOB_NAME_PATTERN.fullmatch(job_name):
        raise ValueError(f"invalid MAST job name: {job_name!r}")
    if not UNIT_NAME_PATTERN.fullmatch(evolution_unit):
        raise ValueError(f"invalid systemd unit name: {evolution_unit!r}")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    if (optimization_marker is None) != (expected_training_steps is None):
        raise ValueError(
            "optimization_marker and expected_training_steps must be set together"
        )
    if optimization_marker is not None and not optimization_marker.is_absolute():
        raise ValueError("optimization_marker must be absolute")
    if expected_training_steps is not None and expected_training_steps < 0:
        raise ValueError("expected_training_steps must be nonnegative")
    if (admission_file is None) != (training_activity is None):
        raise ValueError("admission_file and training_activity must be set together")
    if heartbeat_max_age_seconds <= poll_seconds:
        raise ValueError("heartbeat_max_age_seconds must exceed poll_seconds")
    for path in (admission_file, training_activity):
        if path is not None and not path.is_absolute():
            raise ValueError("admission_file and training_activity must be absolute")

    def admission(allowed: bool, reason: str) -> None:
        if admission_file is not None:
            write_admission_status(
                admission_file,
                allow_admission=allowed,
                reason=reason,
                lease_seconds=heartbeat_max_age_seconds,
            )

    admission(False, "waiting for training")

    previous_state: str | None = None
    while True:
        if (
            optimization_marker is not None
            and expected_training_steps is not None
            and optimization_is_complete(
                optimization_marker,
                expected_training_steps=expected_training_steps,
            )
        ):
            admission(False, OPTIMIZATION_COMPLETE)
            print(
                "Optimization is complete; "
                f"stopping {evolution_unit} before final validation finishes",
                flush=True,
            )
            stop_unit(evolution_unit)
            _write_lifecycle_status(
                status_file,
                job_name=job_name,
                evolution_unit=evolution_unit,
                state=OPTIMIZATION_COMPLETE,
                evolution_stopped=True,
            )
            return OPTIMIZATION_COMPLETE
        try:
            state = query_state(job_name)
        except MastStatusError as error:
            admission(False, "status unavailable")
            print(f"MAST lifecycle query failed; retrying: {error}", flush=True)
            sleep(poll_seconds)
            continue

        if state not in KNOWN_STATES:
            raise MastStatusError(f"query returned unknown state {state!r}")
        allowed = (
            state == "RUNNING"
            and training_activity is not None
            and training_is_active(
                training_activity, max_age_seconds=heartbeat_max_age_seconds
            )
        )
        admission(allowed, "training" if allowed else state)
        if state != previous_state:
            print(f"MAST job {job_name}: state={state}", flush=True)
            previous_state = state
        _write_lifecycle_status(
            status_file,
            job_name=job_name,
            evolution_unit=evolution_unit,
            state=state,
            evolution_stopped=False,
        )

        if state in TERMINAL_STATES:
            print(
                f"MAST job {job_name} is terminal ({state}); stopping {evolution_unit}",
                flush=True,
            )
            stop_unit(evolution_unit)
            _write_lifecycle_status(
                status_file,
                job_name=job_name,
                evolution_unit=evolution_unit,
                state=state,
                evolution_stopped=True,
            )
            return state
        sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--evolution-unit", required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--query-timeout-seconds", type=int, default=30)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--optimization-marker", type=Path)
    parser.add_argument("--expected-training-steps", type=int)
    parser.add_argument("--admission-file", type=Path)
    parser.add_argument("--training-activity", type=Path)
    parser.add_argument("--heartbeat-max-age-seconds", type=int, default=180)
    args = parser.parse_args()
    if args.query_timeout_seconds <= 0:
        parser.error("--query-timeout-seconds must be positive")

    def query(job_name: str) -> str:
        return query_mast_state(job_name, timeout_seconds=args.query_timeout_seconds)

    watch_job(
        args.job_name,
        args.evolution_unit,
        poll_seconds=args.poll_seconds,
        status_file=args.status_file,
        optimization_marker=args.optimization_marker,
        expected_training_steps=args.expected_training_steps,
        admission_file=args.admission_file,
        training_activity=args.training_activity,
        heartbeat_max_age_seconds=args.heartbeat_max_age_seconds,
        query_state=query,
    )


if __name__ == "__main__":
    main()
