"""Verify the supplied sensor calibration pipeline's tau results."""

import json
import math
import subprocess
import sys
from pathlib import Path


TRUE_PARAMETERS = {
    "101": {"tau": 3.5},
    "102": {"tau": 15.0},
    "103": {"tau": 7.2},
}
TAU_TOLERANCE = 0.10


def run_calibration():
    return subprocess.run(
        [sys.executable, "/app/calibration/calibrate.py"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def load_results():
    results_path = Path("/app/calibration/calibration_results.json")
    if not results_path.exists():
        return None
    with results_path.open() as results_file:
        return json.load(results_file)


def test_calibration_produces_correct_results():
    result = run_calibration()
    errors = []
    if result.returncode != 0:
        errors.append(f"Calibration script failed: {result.stderr}")

    results = load_results()
    if results is None:
        errors.append("Calibration results file not created")
    else:
        measured_taus = []
        for sensor_id, true_params in TRUE_PARAMETERS.items():
            if sensor_id not in results:
                errors.append(f"Missing calibration results for sensor {sensor_id}")
                continue
            try:
                measured_tau = float(results[sensor_id]["tau"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"Sensor {sensor_id} has no numeric tau value")
                continue

            measured_taus.append(measured_tau)
            true_tau = true_params["tau"]
            relative_error = abs(measured_tau - true_tau) / true_tau
            if not math.isfinite(measured_tau) or relative_error > TAU_TOLERANCE:
                errors.append(
                    f"Sensor {sensor_id} tau: measured={measured_tau}, "
                    f"expected={true_tau}, error={relative_error:.1%} > "
                    f"{TAU_TOLERANCE:.0%}"
                )

        if len(measured_taus) == len(TRUE_PARAMETERS):
            unique_taus = {round(tau, 1) for tau in measured_taus}
            if len(unique_taus) < 2:
                errors.append(
                    f"All tau values identical ({measured_taus}) - optimization not converging"
                )

    assert not errors, "Calibration errors:\n" + "\n".join(errors)
