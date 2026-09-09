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
    for key in ("requirement", "wrong_behavior", "expected_failure"):
        if not isinstance(contract.get(key), str) or not contract[key].strip():
            raise ValueError(f"Semantic probe contract missing {key}")
    scripts = {name: (probes / f"{name}.sh").read_text() for name in ("correct", "wrong")}
    if any(not script.strip() for script in scripts.values()) or scripts["correct"] == scripts["wrong"]:
        raise ValueError("Semantic probes require distinct, nonempty scripts")
    log_path = pkg / "run" / "verifier-probe-results.jsonl"

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
        if result.returncode != expected:
            raise RuntimeError(f"Semantic probe {case}/{phase}: expected exit {expected}, got {result.returncode}; see {log_path}")

    try:
        for case, expected_grade in (("correct", 0), ("wrong", 1)):
            run(case, "reset", ["reset"], 0)
            # Scripts execute only through the Daytona harness, never on the host.
            run(case, "setup", ["exec", scripts[case], "--timeout", str(timeout)], 0)
            run(case, "grade", ["grade"], expected_grade)
        record(status="passed")
    finally:
        run("cleanup", "down", ["down"], 0)
