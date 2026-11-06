#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py
import json
import os
import pytest

def test_matches_json_exists_and_metric():
    matches_path = "/home/user/matches.json"
    assert os.path.exists(matches_path), f"File not found: {matches_path}"

    with open(matches_path, 'r') as f:
        try:
            matches = json.load(f)
        except json.JSONDecodeError as e:
            pytest.fail(f"Failed to parse {matches_path}: {e}")

    assert isinstance(matches, list), f"Expected list in {matches_path}, got {type(matches)}"

    ground_truth_ids = ["101", "102", "103", "104"]
    correct = 0
    total = len(ground_truth_ids)

    predicted_ids = []
    for m in matches:
        assert isinstance(m, dict), "Each item in matches.json must be a dictionary"
        assert "ocr_text" in m, "Missing key 'ocr_text' in matches.json"
        assert "matched_product_id" in m, "Missing key 'matched_product_id' in matches.json"
        predicted_ids.append(str(m.get("matched_product_id", "")))

    for gt in ground_truth_ids:
        if gt in predicted_ids:
            correct += 1

    accuracy = correct / total
    assert accuracy >= 0.75, f"Match accuracy is {accuracy}, expected >= 0.75"

def test_report_txt_exists():
    report_path = "/home/user/report.txt"
    assert os.path.exists(report_path), f"File not found: {report_path}"
    with open(report_path, 'r') as f:
        content = f.read().strip()
    assert len(content) > 0, f"{report_path} is empty"
    lines = content.split('\n')
    for line in lines:
        if line.strip():
            assert line.startswith("Match: ["), f"Line in report.txt does not match template: {line}"
            assert "] " in line, f"Line in report.txt does not match template: {line}"
            assert " <- " in line, f"Line in report.txt does not match template: {line}"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
