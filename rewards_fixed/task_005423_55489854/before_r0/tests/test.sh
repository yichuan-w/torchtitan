#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py
import os
import json
import math
import pytest

BASE_DIR = "/home/user/metrics_service"
REPORT_PATH = os.path.join(BASE_DIR, "report.json")
AGGREGATOR_PATH = os.path.join(BASE_DIR, "aggregator.py")

def test_report_exists_and_valid():
    assert os.path.isfile(REPORT_PATH), f"Report file not found at {REPORT_PATH}"

    with open(REPORT_PATH, 'r') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            pytest.fail("report.json is not valid JSON.")

    assert "mean" in data, "Key 'mean' missing from report.json."
    assert "stddev" in data, "Key 'stddev' missing from report.json."

def test_report_values():
    assert os.path.isfile(REPORT_PATH), f"Report file not found at {REPORT_PATH}"

    with open(REPORT_PATH, 'r') as f:
        data = json.load(f)

    stddev = data.get("stddev")
    assert stddev is not None, "stddev is null or missing."
    assert isinstance(stddev, (int, float)), "stddev must be a numeric value."
    assert not math.isnan(stddev), "stddev is NaN, which means numerical instability was not fixed."
    assert stddev >= 0, "stddev is less than 0, which is mathematically invalid for standard deviation."

def test_aggregator_assertion():
    assert os.path.isfile(AGGREGATOR_PATH), f"Aggregator script not found at {AGGREGATOR_PATH}"

    with open(AGGREGATOR_PATH, 'r') as f:
        content = f.read()

    assert "assert " in content, "No 'assert' statement found in aggregator.py."
    assert "isnan" in content, "No 'isnan' check found in aggregator.py. You should use math.isnan to check for NaN."
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
