#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
import json
import math
import os
import sqlite3
import struct
import subprocess

import pytest
import requests
from scipy import stats

DB_PATH = "/home/user/data/embeddings.db"
RAW_TEXTS_PATH = "/home/user/raw_texts.json"
BINARY_PATH = "/app/embed_oracle"
BASE_URL = "http://127.0.0.1:8080"
ORIGINAL_TEXTS = [f"This is sample sentence number {number} for the embedding task." for number in range(1, 51)]


def get_embedding(text):
    process = subprocess.run([BINARY_PATH], input=text.encode(), capture_output=True, check=True)
    return struct.unpack("64f", process.stdout)


def unpack_embedding(value):
    return struct.unpack("64f", value)


def cosine_similarity(left, right):
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def connect_with_vec():
    connection = sqlite3.connect(DB_PATH)
    connection.enable_load_extension(True)
    try:
        import sqlite_vec
        sqlite_vec.load(connection)
    except (ImportError, sqlite3.Error):
        pass
    return connection


def vec_tables(connection):
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND lower(sql) LIKE '%using vec0%'"
    ).fetchall()
    return [(name, sql) for name, sql in rows if name]


def populated_index_layout(connection):
    docs_vectors = sorted(tuple(unpack_embedding(blob)) for _, _, blob in docs_rows())
    for table, _ in vec_tables(connection):
        quoted = table.replace('"', '""')
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{quoted}")')]
        rows = connection.execute(f'SELECT * FROM "{quoted}"').fetchall()
        for position, column in enumerate(columns):
            values = [row[position] for row in rows]
            if values and all(isinstance(value, bytes) and len(value) == 256 for value in values):
                vectors = sorted(tuple(unpack_embedding(value)) for value in values)
                if vectors == docs_vectors:
                    return table, columns, position
    return None


def response_json(method, path, **kwargs):
    try:
        response = requests.request(method, f"{BASE_URL}{path}", timeout=5, **kwargs)
    except requests.exceptions.RequestException as error:
        pytest.fail(f"Failed to connect to {path} endpoint: {error}")
    assert response.status_code == 200, f"Expected HTTP 200, got {response.status_code}"
    try:
        return response.json()
    except ValueError:
        pytest.fail(f"Response from {path} is not valid JSON")


def docs_rows():
    with sqlite3.connect(DB_PATH) as connection:
        return connection.execute("SELECT id, text, embedding FROM docs").fetchall()


def test_database_setup():
    assert os.path.exists(DB_PATH), f"Database not found at {DB_PATH}"
    connection = sqlite3.connect(DB_PATH)
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone(), "Table 'docs' does not exist"
    columns = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(docs)")}
    assert "id" in columns, "Column 'id' missing"
    assert "text" in columns, "Column 'text' missing"
    assert "embedding" in columns, "Column 'embedding' missing"
    rows = connection.execute("SELECT id, text, embedding FROM docs").fetchall()
    assert len(rows) == len(ORIGINAL_TEXTS), f"Expected {len(ORIGINAL_TEXTS)} rows in docs table, got {len(rows)}"
    expected = sorted((text, tuple(get_embedding(text))) for text in ORIGINAL_TEXTS)
    actual = sorted((text, unpack_embedding(blob)) for _, text, blob in rows if isinstance(blob, bytes) and len(blob) == 256)
    assert actual == expected, "docs must contain every supplied text and its oracle-produced 64-float embedding"


def test_persistent_vector_index():
    connection = connect_with_vec()
    assert populated_index_layout(connection), "a persistent SQLite vector index must contain every document embedding"


def test_stats_endpoint():
    data = response_json("GET", "/stats")
    for key in ["mean_similarity", "p_value", "ci_lower", "ci_upper"]:
        assert key in data, f"Key '{key}' missing from /stats response"
        assert isinstance(data[key], (int, float)), f"Value for '{key}' should be a number"
    vectors = [unpack_embedding(blob) for _, _, blob in docs_rows()]
    similarities = [cosine_similarity(vectors[i], vectors[j]) for i in range(len(vectors)) for j in range(i + 1, len(vectors))]
    result = stats.ttest_1samp(similarities, 0.0)
    interval = stats.t.interval(0.95, len(similarities) - 1, loc=sum(similarities) / len(similarities), scale=stats.sem(similarities))
    expected = dict(mean_similarity=sum(similarities) / len(similarities), p_value=result.pvalue, ci_lower=interval[0], ci_upper=interval[1])
    assert all(math.isclose(float(data[key]), float(value), rel_tol=1e-6, abs_tol=1e-8) for key, value in expected.items()), "stats values do not match the required pairwise cosine analysis"


def test_search_endpoint():
    query_text = "test query"
    data = response_json("POST", "/search", json={"query": query_text, "top_k": 3})
    assert isinstance(data, list), "Response from /search should be a list"
    assert len(data) == 3, f"Expected 3 results, got {len(data)}"
    for item in data:
        assert "id" in item, "Key 'id' missing from search result"
        assert "text" in item, "Key 'text' missing from search result"
        assert "score" in item, "Key 'score' missing from search result"
        assert isinstance(item["score"], (int, float)), "Score should be a number"
    scores = [item["score"] for item in data]
    assert scores == sorted(scores, reverse=True), "Search results are not sorted by score in descending order"
    query = get_embedding(query_text)
    expected = sorted(((cosine_similarity(query, unpack_embedding(blob)), doc_id, text) for doc_id, text, blob in docs_rows()), reverse=True)[:3]
    assert all(got["id"] == doc_id and got["text"] == text and math.isclose(got["score"], score, rel_tol=1e-5, abs_tol=1e-6) for got, (score, doc_id, text) in zip(data, expected)), "search results must be the requested cosine-nearest documents"


def test_search_queries_vector_index():
    baseline = response_json("POST", "/search", json={"query": "test query", "top_k": 1})
    target_id = baseline[0]["id"]
    connection = connect_with_vec()
    table, columns, vector_position = populated_index_layout(connection)
    quoted = table.replace('"', '""')
    target_blob = next(blob for doc_id, _, blob in docs_rows() if doc_id == target_id)
    indexed = connection.execute(f'SELECT * FROM "{quoted}"').fetchall()
    values = next(row for row in indexed if row[vector_position] == target_blob)
    key_position = next((position for position, row in enumerate(connection.execute(f'PRAGMA table_info("{quoted}")')) if row[5]), 0)
    key_name = columns[key_position].replace('"', '""')
    connection.execute(f'DELETE FROM "{quoted}" WHERE "{key_name}"=?', (values[key_position],))
    connection.commit()
    try:
        changed = response_json("POST", "/search", json={"query": "test query", "top_k": 1})
    finally:
        names = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(f'INSERT INTO "{quoted}"({names}) VALUES ({placeholders})', values)
        connection.commit()
    assert changed and changed[0]["id"] != target_id, "search did not query the persistent SQLite vector index"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
