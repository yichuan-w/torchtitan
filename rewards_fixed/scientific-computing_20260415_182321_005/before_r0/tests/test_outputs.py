import subprocess
import sys
from pathlib import Path

import pytest


def test_simulation_completes():
    """Test that the simulation completes successfully without hanging."""
    script_path = Path("/app/kinetics/simulate.py")
    assert script_path.exists(), "Simulation script not found at /app/kinetics/simulate.py"
    
    # Run the simulation with a timeout to detect hanging
    # Use system python3 which has scipy installed
    result = subprocess.run(
        ["/usr/bin/python3", str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,  # Should complete in seconds with proper solver
        cwd="/app/kinetics"
    )
    
    assert result.returncode == 0, f"Simulation failed with error: {result.stderr}"
    assert "Simulation completed successfully" in result.stdout, \
        f"Simulation did not complete successfully. Output: {result.stdout}"


def test_results_file_exists():
    """Test that the results file is created."""
    results_path = Path("/app/kinetics/results.npz")
    assert results_path.exists(), "Results file not found at /app/kinetics/results.npz"


def test_results_correctness():
    """Test that the simulation produces correct final concentrations."""
    import numpy as np
    
    results_path = Path("/app/kinetics/results.npz")
    assert results_path.exists(), "Results file not found"
    
    data = np.load(results_path)
    
    # Check that all required arrays exist
    assert 't' in data, "Time array 't' not found in results"
    assert 'y1' in data, "Species y1 array not found in results"
    assert 'y2' in data, "Species y2 array not found in results"
    assert 'y3' in data, "Species y3 array not found in results"
    
    # Check time span
    t = data['t']
    assert t[0] == 0.0, f"Initial time should be 0, got {t[0]}"
    assert t[-1] >= 1e5, f"Final time should be at least 1e5, got {t[-1]}"
    
    # Check final concentrations (approximately)
    # Expected values from scipy issue #11274:
    # y1 ≈ 0.0179, y2 ≈ 7.3e-08, y3 ≈ 0.982
    y1_final = data['y1'][-1]
    y2_final = data['y2'][-1]
    y3_final = data['y3'][-1]
    
    # Allow some tolerance for numerical differences between solvers
    assert abs(y1_final - 0.0179) < 0.01, \
        f"y1 final concentration should be ~0.0179, got {y1_final}"
    assert abs(y2_final - 7.3e-08) < 5e-07, \
        f"y2 final concentration should be ~7.3e-08, got {y2_final}"
    assert abs(y3_final - 0.982) < 0.01, \
        f"y3 final concentration should be ~0.982, got {y3_final}"
    
    # Check mass conservation: y1 + y2 + y3 should be close to 1.0
    total = y1_final + y2_final + y3_final
    assert abs(total - 1.0) < 0.01, \
        f"Mass conservation violated: y1+y2+y3 = {total}, expected ~1.0"


def test_initial_conditions():
    """Test that initial conditions are correctly set."""
    import numpy as np
    
    results_path = Path("/app/kinetics/results.npz")
    assert results_path.exists(), "Results file not found"
    
    data = np.load(results_path)
    
    y1_initial = data['y1'][0]
    y2_initial = data['y2'][0]
    y3_initial = data['y3'][0]
    
    assert abs(y1_initial - 1.0) < 1e-10, f"y1 initial should be 1.0, got {y1_initial}"
    assert abs(y2_initial - 0.0) < 1e-10, f"y2 initial should be 0.0, got {y2_initial}"
    assert abs(y3_initial - 0.0) < 1e-10, f"y3 initial should be 0.0, got {y3_initial}"


def test_analyze_script_exists():
    """Test that the analyze script is created."""
    analyze_path = Path("/app/kinetics/analyze.py")
    assert analyze_path.exists(), "Analyze script not found at /app/kinetics/analyze.py"


def test_analyze_script_runs():
    """Test that the analyze script runs and produces correct output."""
    analyze_path = Path("/app/kinetics/analyze.py")
    assert analyze_path.exists(), "Analyze script not found"
    
    # Run the analyze script
    result = subprocess.run(
        ["/usr/bin/python3", str(analyze_path)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd="/app/kinetics"
    )
    
    assert result.returncode == 0, f"Analyze script failed: {result.stderr}"
    
    # Check that output contains required information
    output = result.stdout.lower()
    assert "final" in output or "concentration" in output, \
        f"Analyze output should mention final concentrations. Output: {result.stdout}"
    assert "maximum" in output or "max" in output or "peak" in output, \
        f"Analyze output should mention maximum y2. Output: {result.stdout}"
    assert "mass" in output or "conservation" in output, \
        f"Analyze output should verify mass conservation. Output: {result.stdout}"


def test_y2_maximum_analysis():
    """Test that y2 maximum is correctly identified."""
    import numpy as np
    
    results_path = Path("/app/kinetics/results.npz")
    assert results_path.exists(), "Results file not found"
    
    data = np.load(results_path)
    y2 = data['y2']
    t = data['t']
    
    # Find the actual maximum
    max_idx = np.argmax(y2)
    y2_max = y2[max_idx]
    t_at_max = t[max_idx]
    
    # y2 should have a measurable peak
    assert y2_max > 1e-7, f"y2 maximum should be significant, got {y2_max}"
    # The peak should be detected (may not be at exact time due to sampling)
    # Just verify that y2 has non-trivial variation
    assert np.std(y2) > 1e-10, f"y2 should show variation over time"


if __name__ == "__main__":
    pytest.main([__file__])
