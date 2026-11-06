#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
import csv
import difflib
import itertools
import json
import os
import re

import pytest


MATCHES_PATH = "/home/user/matches.json"
REPORT_PATH = "/home/user/report.txt"
EXPECTED_RAW_LINES = [
    "1x Wdget Pro X",
    "2x Blndr 500w Blck",
    "1x Cff Makr Dlux",
    "4x SuperPhone 128GB",
]


def normalized(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def best_raw_assignment(raw_lines):
    scores = [
        [difflib.SequenceMatcher(None, normalized(raw), normalized(expected)).ratio()
         for expected in EXPECTED_RAW_LINES]
        for raw in raw_lines
    ]
    return max(
        itertools.permutations(range(len(EXPECTED_RAW_LINES))),
        key=lambda assignment: sum(scores[index][target] for index, target in enumerate(assignment)),
    ), scores


def test_matches_json_exists_and_metric():
    assert os.path.exists(MATCHES_PATH), f"File not found: {MATCHES_PATH}"
    try:
        with open(MATCHES_PATH, encoding="utf-8") as matches_file:
            matches = json.load(matches_file)
    except json.JSONDecodeError as error:
        pytest.fail(f"Failed to parse {MATCHES_PATH}: {error}")

    assert isinstance(matches, list), f"Expected list in {MATCHES_PATH}, got {type(matches)}"
    assert len(matches) == 4, "matches.json must contain the four non-empty manifest lines"

    predicted_ids = []
    raw_lines = []
    for match in matches:
        assert isinstance(match, dict), "Each item in matches.json must be a dictionary"
        assert set(match) == {"ocr_text", "matched_product_id"}, "Each match must have exactly the required keys"
        predicted_ids.append(str(match["matched_product_id"]))
        raw_lines.append(match["ocr_text"])

    assert all(isinstance(raw, str) and raw.strip() for raw in raw_lines), "ocr_text must contain a non-empty raw OCR line"
    assignment, scores = best_raw_assignment(raw_lines)
    assert all(
        scores[index][target] >= 0.58
        and re.match(rf"^\s*{EXPECTED_RAW_LINES[target][0]}\s*[xX]\b", raw_lines[index])
        for index, target in enumerate(assignment)
    ), "ocr_text must preserve each supplied image line, including its quantity prefix"

    correct = sum(product_id in predicted_ids for product_id in ["101", "102", "103", "104"])
    assert correct / 4 >= 0.75, f"Match accuracy is {correct / 4}, expected >= 0.75"


def test_report_txt_exists_and_matches_json():
    assert os.path.exists(REPORT_PATH), f"File not found: {REPORT_PATH}"
    with open(REPORT_PATH, encoding="utf-8") as report_file:
        content = report_file.read().strip()
    assert len(content) > 0, f"{REPORT_PATH} is empty"

    with open(MATCHES_PATH, encoding="utf-8") as matches_file:
        matches = json.load(matches_file)
    with open("/app/catalog.csv", newline="", encoding="utf-8") as catalog_file:
        catalog = {str(row["product_id"]): normalized(row["product_name"]) for row in csv.DictReader(catalog_file)}

    lines = content.split("\n")
    assert len(lines) == len(matches), "report.txt must contain one line per match"
    parsed = []
    for line in lines:
        template_match = re.fullmatch(r"Match: \[([^\]]+)\] (.*?) <- (.*)", line)
        assert template_match is not None, f"Line in report.txt does not match template: {line}"
        product_id, catalog_name, raw_text = template_match.groups()
        parsed.append((product_id, normalized(catalog_name), raw_text))

    expected = [
        (str(match["matched_product_id"]), catalog.get(str(match["matched_product_id"])), match["ocr_text"])
        for match in matches
    ]
    assert sorted(parsed) == sorted(expected), "Report IDs, normalized catalog names, and raw OCR text must match matches.json"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
