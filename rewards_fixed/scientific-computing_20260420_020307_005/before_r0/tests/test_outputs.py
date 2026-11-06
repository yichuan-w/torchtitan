"""
Tests for sensor calibration pipeline.

Verifies that calibration parameters are correctly estimated after fixing
the numerical precision issue with float32 Pandas Series in scipy.optimize.curve_fit.
"""

import json
import subprocess
import sys
from pathlib import Path


# True parameters that calibration should recover (within tolerance)
TRUE_PARAMETERS = {
    "101": {"T_initial": 25.0, "T_final": 75.0, "tau": 3.5},
    "102": {"T_initial": 80.0, "T_final": 20.0, "tau": 15.0},
    "103": {"T_initial": 15.0, "T_final": 95.0, "tau": 7.2},
}

# Relative error tolerance for tau (10%)
TAU_TOLERANCE = 0.10
# Temperature tolerance (degrees Celsius)
TEMP_TOLERANCE = 1.5


def run_calibration():
    """Run the calibration script and return the result."""
    return subprocess.run(
        [sys.executable, "/app/calibration/calibrate.py"],
        capture_output=True,
        text=True,
        timeout=60
    )


def load_results():
    """Load calibration results from JSON file."""
    results_path = Path("/app/calibration/calibration_results.json")
    if not results_path.exists():
        return None
    with open(results_path, 'r') as f:
        return json.load(f)


def test_calibration_produces_correct_results():
    """
    Main correctness test - verifies calibration produces correct parameters.
    This test MUST fail initially because the float32 bug causes incorrect tau values.
    """
    # Run calibration
    result = run_calibration()
    assert result.returncode == 0, f"Calibration script failed: {result.stderr}"
    
    # Load results
    results = load_results()
    assert results is not None, "Calibration results file not created"
    
    # Check all sensors present
    for sensor_id in ["101", "102", "103"]:
        assert sensor_id in results, f"Missing calibration results for sensor {sensor_id}"
    
    errors = []
    
    # Verify tau accuracy
    for sensor_id, true_params in TRUE_PARAMETERS.items():
        measured_tau = results[sensor_id]["tau"]
        true_tau = true_params["tau"]
        relative_error = abs(measured_tau - true_tau) / true_tau
        
        if relative_error > TAU_TOLERANCE:
            errors.append(
                f"Sensor {sensor_id} tau: measured={measured_tau:.2f}, expected={true_tau}, "
                f"error={relative_error:.1%} > {TAU_TOLERANCE:.0%}"
            )
    
    # Verify tau values are different across sensors
    tau_values = [results[sid]["tau"] for sid in ["101", "102", "103"]]
    unique_tau = set(round(t, 1) for t in tau_values)
    if len(unique_tau) < 2:
        errors.append(
            f"All tau values identical ({tau_values}) - optimization not converging"
        )
    
    # Verify temperature parameters
    for sensor_id, true_params in TRUE_PARAMETERS.items():
        for param in ["T_initial", "T_final"]:
            measured = results[sensor_id][param]
            true_val = true_params[param]
            
            if abs(measured - true_val) > TEMP_TOLERANCE:
                errors.append(
                    f"Sensor {sensor_id} {param}: measured={measured:.2f}, "
                    f"expected={true_val}, error={abs(measured - true_val):.2f}°C"
                )
    
    assert len(errors) == 0, "Calibration errors:\n" + "\n".join(errors)
