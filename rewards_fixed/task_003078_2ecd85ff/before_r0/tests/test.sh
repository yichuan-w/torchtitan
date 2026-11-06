#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py

import os
import json
import sqlite3
import struct
import subprocess
import math
import requests
import pytest

DB_PATH = "/home/user/data/embeddings.db"
RAW_TEXTS_PATH = "/home/user/raw_texts.json"
BINARY_PATH = "/app/embed_oracle"
BASE_URL = "http://127.0.0.1:8080"

def get_embedding(text):
    p = subprocess.run([BINARY_PATH], input=text.encode('utf-8'), capture_output=True, check=True)
    floats = struct.unpack(f"{len(p.stdout)//4}f", p.stdout)
    return floats

def cosine_similarity(v1, v2):
    dot = sum(x*y for x, y in zip(v1, v2))
    norm1 = math.sqrt(sum(x*x for x in v1))
    norm2 = math.sqrt(sum(x*x for x in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)

def test_database_setup():
    assert os.path.exists(DB_PATH), f"Database not found at {DB_PATH}"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='docs'")
    assert cursor.fetchone() is not None, "Table 'docs' does not exist in the database"

    cursor.execute("PRAGMA table_info(docs)")
    columns = {row[1]: row[2] for row in cursor.fetchall()}
    assert 'id' in columns, "Column 'id' missing"
    assert 'text' in columns, "Column 'text' missing"
    assert 'embedding' in columns, "Column 'embedding' missing"

    with open(RAW_TEXTS_PATH, 'r') as f:
        raw_texts = json.load(f)

    cursor.execute("SELECT count(*) FROM docs")
    count = cursor.fetchone()[0]
    assert count == len(raw_texts), f"Expected {len(raw_texts)} rows in docs table, got {count}"

    conn.close()

def test_stats_endpoint():
    try:
        response = requests.get(f"{BASE_URL}/stats", timeout=5)
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Failed to connect to /stats endpoint: {e}")

    assert response.status_code == 200, f"Expected HTTP 200, got {response.status_code}"

    try:
        data = response.json()
    except ValueError:
        pytest.fail("Response from /stats is not valid JSON")

    for key in ["mean_similarity", "p_value", "ci_lower", "ci_upper"]:
        assert key in data, f"Key '{key}' missing from /stats response"
        assert isinstance(data[key], (int, float)), f"Value for '{key}' should be a number"

def test_search_endpoint():
    query_text = "test query"
    payload = {"query": query_text, "top_k": 3}

    try:
        response = requests.post(f"{BASE_URL}/search", json=payload, timeout=5)
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Failed to connect to /search endpoint: {e}")

    assert response.status_code == 200, f"Expected HTTP 200, got {response.status_code}"

    try:
        data = response.json()
    except ValueError:
        pytest.fail("Response from /search is not valid JSON")

    assert isinstance(data, list), "Response from /search should be a list"
    assert len(data) == 3, f"Expected 3 results, got {len(data)}"

    for item in data:
        assert "id" in item, "Key 'id' missing from search result"
        assert "text" in item, "Key 'text' missing from search result"
        assert "score" in item, "Key 'score' missing from search result"
        assert isinstance(item["score"], (int, float)), "Score should be a number"

    # Verify scores are sorted descending
    scores = [item["score"] for item in data]
    assert scores == sorted(scores, reverse=True), "Search results are not sorted by score in descending order"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
