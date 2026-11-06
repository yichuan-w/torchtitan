"""Tests for custom archive recovery task.

Verifies that all files were correctly recovered from the corrupted ARCV1 archive.
"""
import os
import json
import hashlib


RECOVERED_DIR = "/app/recovered"
EXPECTED_FILES = ["config.json", "data.csv", "notes.txt", "pipeline.log", "readme.txt"]


def test_recovered_directory_exists():
    """The recovered/ directory must exist."""
    assert os.path.isdir(RECOVERED_DIR), (
        f"Directory {RECOVERED_DIR} does not exist. "
        "Create it and place recovered files there."
    )


def test_all_files_recovered():
    """All 5 expected files must be present in the recovered directory."""
    for fname in EXPECTED_FILES:
        path = os.path.join(RECOVERED_DIR, fname)
        assert os.path.isfile(path), (
            f"Missing recovered file: {fname}. Expected at {path}"
        )


def test_readme_content():
    """Verify readme.txt has correct content."""
    path = os.path.join(RECOVERED_DIR, "readme.txt")
    assert os.path.isfile(path), f"File not found: {path}"

    with open(path, "rb") as f:
        content = f.read()

    text = content.decode("utf-8")
    assert "IMPORTANT RECOVERY README" in text, "readme.txt is missing the title line"
    assert "Alpha-7 Data Pipeline" in text, "readme.txt is missing project name"
    assert "ZIP-RECOVER-ALPHA7-8F3A" in text, "readme.txt is missing the recovery key"
    assert "ALL FILES RECOVERED" in text, "readme.txt is missing status line"
    assert len(content) == 195, (
        f"readme.txt has wrong size: expected 195 bytes, got {len(content)}"
    )


def test_config_json_content():
    """Verify config.json is valid JSON with expected structure and values."""
    path = os.path.join(RECOVERED_DIR, "config.json")
    assert os.path.isfile(path), f"File not found: {path}"

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["project"] == "Alpha-7", (
        f"config.json: expected project='Alpha-7', got '{data.get('project')}'"
    )
    assert data["version"] == "2.4.1", (
        f"config.json: expected version='2.4.1', got '{data.get('version')}'"
    )
    assert data["database"]["host"] == "db.alpha7.internal", (
        "config.json: wrong database host"
    )
    assert data["database"]["port"] == 5432, (
        "config.json: wrong database port"
    )

    # Verify exact file size
    with open(path, "rb") as f:
        raw = f.read()
    assert len(raw) == 121, (
        f"config.json has wrong size: expected 121 bytes, got {len(raw)}"
    )


def test_data_csv_content():
    """Verify data.csv has correct structure and values."""
    path = os.path.join(RECOVERED_DIR, "data.csv")
    assert os.path.isfile(path), f"File not found: {path}"

    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    assert len(lines) == 4, (
        f"data.csv: expected 4 lines (1 header + 3 data), got {len(lines)}"
    )
    assert lines[0] == "id,timestamp,sensor,value,unit,status", (
        f"data.csv: wrong header: {lines[0]}"
    )
    assert lines[1].startswith("1001,2024-03-15T08:00:00Z,TEMP-A1,23.5,celsius,ok"), (
        f"data.csv: row 1 wrong: {lines[1]}"
    )
    assert lines[3].startswith("1003,2024-03-15T08:10:00Z,TEMP-A1,23.6,celsius,ok"), (
        f"data.csv: row 3 wrong: {lines[3]}"
    )

    with open(path, "rb") as f:
        raw = f.read()
    assert len(raw) == 188, (
        f"data.csv has wrong size: expected 188 bytes, got {len(raw)}"
    )


def test_notes_content():
    """Verify notes.txt has correct content."""
    path = os.path.join(RECOVERED_DIR, "notes.txt")
    assert os.path.isfile(path), f"File not found: {path}"

    with open(path, "rb") as f:
        content = f.read()

    text = content.decode("utf-8")
    assert "Field Notes - March 15, 2024" in text, "notes.txt: missing title"
    assert "TEMP-A1 drift detected" in text, "notes.txt: missing TEMP-A1 note"
    assert "HUMID-B2 warning" in text, "notes.txt: missing HUMID-B2 note"
    assert "J. Martinez, Field Engineer" in text, "notes.txt: missing signature"

    assert len(content) == 210, (
        f"notes.txt has wrong size: expected 210 bytes, got {len(content)}"
    )


def test_pipeline_log_content():
    """Verify pipeline.log has correct content."""
    path = os.path.join(RECOVERED_DIR, "pipeline.log")
    assert os.path.isfile(path), f"File not found: {path}"

    with open(path, "rb") as f:
        content = f.read()

    text = content.decode("utf-8")
    assert "Data pipeline starting" in text, "pipeline.log: missing start message"
    assert "Loading configuration" in text, "pipeline.log: missing config load"
    assert "Pipeline completed successfully" in text, "pipeline.log: missing completion"
    assert "Memory usage at 78%" in text, "pipeline.log: missing memory warning"

    assert len(content) == 205, (
        f"pipeline.log has wrong size: expected 205 bytes, got {len(content)}"
    )


def test_no_extra_files():
    """Only the 5 expected files should be present (no extra junk)."""
    assert os.path.isdir(RECOVERED_DIR), (
        f"Directory {RECOVERED_DIR} does not exist yet — recover files first."
    )

    recovered_files = []
    for root, dirs, files in os.walk(RECOVERED_DIR):
        for f in files:
            recovered_files.append(os.path.relpath(os.path.join(root, f), RECOVERED_DIR))

    expected_set = set(EXPECTED_FILES)
    actual_set = set(recovered_files)

    extra = actual_set - expected_set
    assert not extra, (
        f"Unexpected files found in recovered/ directory: {extra}. "
        f"Only these files should be present: {EXPECTED_FILES}"
    )

    missing = expected_set - actual_set
    assert not missing, (
        f"Missing expected files in recovered/ directory: {missing}"
    )


def test_archive_unmodified():
    """Verify the original archive was not modified."""
    assert os.path.isdir(RECOVERED_DIR), (
        f"Directory {RECOVERED_DIR} does not exist yet — recover files first."
    )

    path = "/app/data.bin"
    assert os.path.isfile(path), "Original archive is missing"

    # Compare the complete supplied archive, not only its size.
    with open(path, "rb") as f:
        data = f.read()
    digest = hashlib.sha256(data).hexdigest()
    assert digest == "b9ec5506e2aefbd9b3693606539166a20bec67c2261283970d3bee9db9222a79", (
        "Original archive content changed. "
        "Do not modify data.bin — extract files from it instead."
    )
