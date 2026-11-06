#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py
import os
import json
import subprocess
import sys
import math
import numpy as np

def test_files_exist():
    """Check that the required files have been created."""
    assert os.path.isfile('/home/user/synthetic_pipeline.py'), "synthetic_pipeline.py is missing."
    assert os.path.isfile('/home/user/test_pipeline.py'), "test_pipeline.py is missing."
    assert os.path.isfile('/home/user/metrics.json'), "metrics.json is missing."

def test_metrics_json_structure():
    """Check that metrics.json has the correct structure and types."""
    metrics_path = '/home/user/metrics.json'
    if not os.path.isfile(metrics_path):
        return  # handled by test_files_exist

    with open(metrics_path, 'r') as f:
        try:
            metrics = json.load(f)
        except json.JSONDecodeError:
            assert False, "metrics.json is not a valid JSON file."

    expected_keys = {"A", "B", "C"}
    assert set(metrics.keys()) == expected_keys, f"Expected keys {expected_keys} in metrics.json, got {set(metrics.keys())}."

    for feature in expected_keys:
        feature_data = metrics[feature]
        assert isinstance(feature_data, dict), f"Value for {feature} should be a dictionary."

        expected_subkeys = {"distance", "ci_lower", "ci_upper"}
        assert set(feature_data.keys()) == expected_subkeys, f"Expected keys {expected_subkeys} in {feature}, got {set(feature_data.keys())}."

        for subkey in expected_subkeys:
            val = feature_data[subkey]
            assert isinstance(val, (int, float)), f"Value for {feature}.{subkey} should be a number, got {type(val)}."

def test_test_pipeline_content():
    """Check that test_pipeline.py contains an import for calculate_wasserstein and pytest assertions."""
    test_path = '/home/user/test_pipeline.py'
    if not os.path.isfile(test_path):
        return

    with open(test_path, 'r') as f:
        content = f.read()

    assert 'calculate_wasserstein' in content, "test_pipeline.py must import/use calculate_wasserstein."
    assert 'assert ' in content, "test_pipeline.py must contain pytest assertions."

def test_test_pipeline_passes():
    """Run pytest on test_pipeline.py and ensure it passes."""
    test_path = '/home/user/test_pipeline.py'
    if not os.path.isfile(test_path):
        return

    result = subprocess.run([sys.executable, '-m', 'pytest', test_path], capture_output=True, text=True)
    assert result.returncode == 0, f"pytest on {test_path} failed.\nStdout: {result.stdout}\nStderr: {result.stderr}"

def test_degenerate_covariance_pipeline():
    """Exercise the reusable pipeline on a centered, rank-one covariance."""
    data_path = '/home/user/original_data.csv'
    metrics_path = '/home/user/metrics.json'
    with open(data_path, 'rb') as source:
        original_data = source.read()

    values = np.linspace(99.0, 101.0, 100)
    fixture = np.column_stack((values, 2.0 * values, np.full(100, -7.0)))
    np.savetxt(data_path, fixture, delimiter=',', header='A,B,C', comments='')
    try:
        result = subprocess.run(
            [sys.executable, '/home/user/synthetic_pipeline.py'],
            capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, f"Pipeline failed for positive-semidefinite covariance.\nStdout: {result.stdout}\nStderr: {result.stderr}"
        with open(metrics_path, 'r') as output:
            metrics = json.load(output)
        numbers = [metrics[feature][field] for feature in ('A', 'B', 'C') for field in ('distance', 'ci_lower', 'ci_upper')]
        assert all(isinstance(value, (int, float)) and math.isfinite(value) for value in numbers), "Degenerate-data metrics must be finite numbers."
        assert all(metrics['C'][field] == 0.0 for field in ('distance', 'ci_lower', 'ci_upper')), "A constant feature must remain on the empirical support."
        assert math.isclose(metrics['B']['distance'], 2.0 * metrics['A']['distance'], rel_tol=1e-10, abs_tol=1e-12), "The rank-one proposal must remain on the empirical support."
        assert 0.0 <= metrics['A']['distance'] < 1.0 and 0.0 <= metrics['A']['ci_lower'] < metrics['A']['ci_upper'] < 1.5, "Metrics must reflect a nondegenerate MVN centered at the empirical mean."
    finally:
        with open(data_path, 'wb') as source:
            source.write(original_data)
        subprocess.run([sys.executable, '/home/user/synthetic_pipeline.py'], capture_output=True, text=True, timeout=60)
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
