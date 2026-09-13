# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Replay verifier-authored semantic controls in fresh Daytona sandboxes."""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class SemanticProbeMisses(RuntimeError):
    def __init__(self, messages: list[str], log_path: Path):
        self.log_path = log_path
        super().__init__("; ".join(messages) + f"; see {log_path}")


class _DaytonaTransportFailure(RuntimeError):
    pass


def verify_probes(pkg: Path, env: dict[str, str], timeout: int) -> None:
    probes = pkg / "run" / "verifier-probes"
    contract = json.loads((probes / "contract.json").read_text())
    cases = contract.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Semantic probe contract requires nonempty cases")
    for case in cases:
        for key in ("requirement", "wrong_behavior", "expected_failure"):
            if (
                not isinstance(case, dict)
                or not isinstance(case.get(key), str)
                or not case[key].strip()
            ):
                raise ValueError(f"Semantic probe contract missing {key}")
    names = ["correct", *(f"wrong-{index}" for index in range(1, len(cases) + 1))]
    scripts = {name: (probes / f"{name}.sh").read_text() for name in names}
    if any(not script.strip() for script in scripts.values()) or len(
        set(scripts.values())
    ) != len(scripts):
        raise ValueError("Semantic probes require distinct, nonempty scripts")
    log_path = pkg / "run" / "verifier-probe-results.jsonl"
    missed = []
    log_lock = threading.Lock()

    def record(**values):
        with log_lock, log_path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                        **values,
                    }
                )
                + "\n"
            )

    def package_hashes():
        return {
            str(p.relative_to(pkg)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in pkg.rglob("*")
            if p.is_file() and "run" not in p.relative_to(pkg).parts
        }

    original_hashes = package_hashes()

    def probe_hashes():
        return {
            name: hashlib.sha256((probes / name).read_bytes()).hexdigest()
            for name in ["contract.json", *(f"{name}.sh" for name in names)]
        }

    original_probe_hashes = probe_hashes()
    record(
        status="start",
        contract=contract,
        scripts=scripts,
        package_sha256=original_hashes,
        probe_sha256=original_probe_hashes,
    )

    def run(
        case: str,
        phase: str,
        args: list[str],
        expected: int,
        attempt: int = 1,
        workspace: Path = pkg,
    ):
        record(
            case=case,
            phase=phase,
            status="start",
            args=args,
            attempt=attempt,
            workspace=str(workspace),
        )
        try:
            result = subprocess.run(
                [str(workspace / "sandbox"), *args],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            record(case=case, phase=phase, status="timeout", attempt=attempt)
            raise
        record(
            case=case,
            phase=phase,
            status="finished",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            attempt=attempt,
        )
        # An unconfirmed command may have changed state; replay from reset, never just re-grade.
        if (
            phase in ("setup", "grade")
            and result.returncode == 2
            and not result.stdout
            and re.fullmatch(
                r"sandbox error: DaytonaBadGatewayError: [^\n]*\n?", result.stderr
            )
        ):
            raise _DaytonaTransportFailure(
                f"Semantic probe {case}/{phase}: {result.stderr.strip()}"
            )
        if phase == "grade":
            # Some graders provide only a reward; preserve details where available.
            record(case=case, phase="grade_details", status="start", attempt=attempt)
            details = subprocess.run(
                [
                    str(workspace / "sandbox"),
                    "exec",
                    "if [ -f /logs/verifier/ctrf.json ]; then cat /logs/verifier/ctrf.json; fi",
                ],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            record(
                case=case,
                phase="grade_details",
                status="finished",
                returncode=details.returncode,
                stdout=details.stdout,
                stderr=details.stderr,
                attempt=attempt,
            )
        if result.returncode != expected:
            message = f"Semantic probe {case}/{phase}: expected exit {expected}, got {result.returncode}"
            if phase == "grade" and (
                (case.startswith("wrong-") and result.returncode == 0)
                or (case == "correct" and result.returncode == 1)
            ):
                # Collect all semantic misses for repair; setup and grading errors still abort.
                missed.append(message)
            else:
                raise RuntimeError(f"{message}; see {log_path}")

    def replay_case(case):
        workspace = workspaces / case
        # Never copy a live container ID into another case's workspace.
        shutil.copytree(
            pkg,
            workspace,
            ignore=lambda directory, entries: {"run"}
            if Path(directory) == pkg
            else set(),
        )
        (workspace / "run").mkdir()
        for name in (
            "seed_size.json",
            "resources.json",
            "seed_literals.json",
            "pretest.json",
        ):
            source = pkg / "run" / name
            if source.exists():
                shutil.copy2(source, workspace / "run" / name)
        try:
            for attempt in (1, 2):
                try:
                    run(case, "reset", ["reset"], 0, attempt, workspace)
                    # Scripts execute only through the Daytona harness, never on the host.
                    run(
                        case,
                        "setup",
                        ["exec", scripts[case], "--timeout", str(timeout)],
                        0,
                        attempt,
                        workspace,
                    )
                    run(
                        case,
                        "grade",
                        ["grade"],
                        0 if case == "correct" else 1,
                        attempt,
                        workspace,
                    )
                    break
                except _DaytonaTransportFailure as error:
                    if attempt == 2:
                        record(
                            case=case,
                            status="transport_failed",
                            attempt=attempt,
                            error=str(error),
                        )
                        raise
                    if (
                        package_hashes() != original_hashes
                        or probe_hashes() != original_probe_hashes
                    ):
                        record(
                            case=case,
                            status="recovery_refused",
                            reason="inputs_changed",
                        )
                        raise RuntimeError(
                            f"Semantic probe inputs changed before recovery; see {log_path}"
                        ) from error
                    record(
                        case=case,
                        status="transport_retry",
                        attempt=attempt + 1,
                        error=str(error),
                        package_sha256=original_hashes,
                        probe_sha256=original_probe_hashes,
                    )
        finally:
            run(case, "down", ["down"], 0, workspace=workspace)

    try:
        workspaces = Path(tempfile.mkdtemp(prefix="probe-cases-", dir=pkg / "run"))
        errors = []
        with ThreadPoolExecutor(max_workers=min(3000, len(names))) as pool:
            futures = {case: pool.submit(replay_case, case) for case in names}
            for case, future in futures.items():
                try:
                    future.result()
                except Exception as error:
                    record(case=case, status="error", error=str(error))
                    errors.append(error)
        if errors:
            raise errors[0]
        if missed:
            missed.sort()
            record(status="failed", errors=missed)
            raise SemanticProbeMisses(missed, log_path)
        record(status="passed")
    finally:
        run("cleanup", "down", ["down"], 0)
