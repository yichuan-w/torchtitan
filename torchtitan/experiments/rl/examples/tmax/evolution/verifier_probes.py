"""Replay verifier-authored semantic controls in fresh Daytona sandboxes."""
from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from pathlib import Path


def verify_probes(pkg: Path, env: dict[str, str], timeout: int) -> None:
    probes = pkg / "run" / "verifier-probes"
    contract = json.loads((probes / "contract.json").read_text())
    cases = contract.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Semantic probe contract requires nonempty cases")
    for case in cases:
        for key in ("requirement", "wrong_behavior", "expected_failure"):
            if not isinstance(case, dict) or not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f"Semantic probe contract missing {key}")
    names = ["correct", *(f"wrong-{index}" for index in range(1, len(cases) + 1))]
    scripts = {name: (probes / f"{name}.sh").read_text() for name in names}
    if any(not script.strip() for script in scripts.values()) or len(set(scripts.values())) != len(scripts):
        raise ValueError("Semantic probes require distinct, nonempty scripts")
    log_path = pkg / "run" / "verifier-probe-results.jsonl"
    missed = []

    def record(**values):
        with log_path.open("a") as stream:
            stream.write(json.dumps({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(), **values}) + "\n")

    record(status="start", contract=contract, scripts=scripts,
           package_sha256={str(p.relative_to(pkg)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in pkg.rglob("*") if p.is_file() and "run" not in p.relative_to(pkg).parts})

    def run(case: str, phase: str, args: list[str], expected: int):
        record(case=case, phase=phase, status="start", args=args)
        try:
            result = subprocess.run([str(pkg / "sandbox"), *args], cwd=pkg, env=env,
                                    capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            record(case=case, phase=phase, status="timeout")
            raise
        record(case=case, phase=phase, status="finished", returncode=result.returncode,
               stdout=result.stdout, stderr=result.stderr)
        if phase == "grade":
            # Some graders provide only a reward; preserve details where available.
            record(case=case, phase="grade_details", status="start")
            details = subprocess.run(
                [str(pkg / "sandbox"), "exec",
                 "if [ -f /logs/verifier/ctrf.json ]; then cat /logs/verifier/ctrf.json; fi"],
                cwd=pkg, env=env, capture_output=True, text=True, timeout=timeout)
            record(case=case, phase="grade_details", status="finished", returncode=details.returncode,
                   stdout=details.stdout, stderr=details.stderr)
        if result.returncode != expected:
            message = f"Semantic probe {case}/{phase}: expected exit {expected}, got {result.returncode}"
            if phase == "grade" and case.startswith("wrong-") and result.returncode == 0:
                # Collect all semantic misses for repair; setup and grading errors still abort.
                missed.append(message)
            else:
                raise RuntimeError(f"{message}; see {log_path}")

    try:
        for case in names:
            run(case, "reset", ["reset"], 0)
            # Scripts execute only through the Daytona harness, never on the host.
            run(case, "setup", ["exec", scripts[case], "--timeout", str(timeout)], 0)
            run(case, "grade", ["grade"], 0 if case == "correct" else 1)
        if missed:
            record(status="failed", errors=missed)
            raise RuntimeError("; ".join(missed) + f"; see {log_path}")
        record(status="passed")
    finally:
        run("cleanup", "down", ["down"], 0)
