#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py
import os
import sqlite3
import pytest

def test_clean_data_csv():
    csv_path = "/home/user/clean_data.csv"
    assert os.path.isfile(csv_path), f"Expected output file {csv_path} does not exist."

    with open(csv_path, "r") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]

    expected_lines = [
        "MaskedEmail,TotalValue",
        "b***@yahoo.com,225.00",
        "a***@gmail.com,200.00",
        "c***@company.co.uk,75.25",
        "d***@short.com,10.00"
    ]

    assert lines == expected_lines, "The content of clean_data.csv does not match the expected aggregated and sorted output."

def test_analytics_db():
    db_path = "/home/user/analytics.db"
    assert os.path.isfile(db_path), f"Expected database file {db_path} does not exist."

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserTotals';")
    table_exists = cursor.fetchone()
    assert table_exists is not None, "Table 'UserTotals' does not exist in analytics.db."

    # Check data
    cursor.execute("SELECT MaskedEmail, TotalValue FROM UserTotals ORDER BY TotalValue DESC;")
    rows = cursor.fetchall()

    expected_rows = [
        ("b***@yahoo.com", 225.0),
        ("a***@gmail.com", 200.0),
        ("c***@company.co.uk", 75.25),
        ("d***@short.com", 10.0)
    ]

    # Format the floats to avoid precision issues in test comparison
    formatted_rows = [(row[0], round(float(row[1]), 2)) for row in rows]
    expected_formatted = [(row[0], round(float(row[1]), 2)) for row in expected_rows]

    assert formatted_rows == expected_formatted, "The data in UserTotals table does not match the expected values."

    conn.close()

def test_c_program_exists():
    assert os.path.isfile("/home/user/process.c"), "/home/user/process.c is missing."
    assert os.path.isfile("/home/user/process"), "The compiled executable /home/user/process is missing."
    assert os.access("/home/user/process", os.X_OK), "/home/user/process is not executable."
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
