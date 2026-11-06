import os
import json
import csv
import io


EXPECTED_README = (
    "Project Alpha - Build Instructions\n\n"
    "1. Install dependencies: pip install -r requirements.txt\n"
    "2. Configure settings in config.json\n"
    "3. Run: python main.py\n"
    "4. Tests: pytest tests/\n\n"
    "Contact: dev@alpha-project.example.com\n"
)

EXPECTED_CSV_HEADER = "id,name,score,active"
EXPECTED_CSV_ROWS = [
    ["1", "Alice", "95.5", "true"],
    ["2", "Bob", "87.3", "true"],
    ["3", "Charlie", "72.1", "false"],
    ["4", "Diana", "91.8", "true"],
    ["5", "Eve", "68.4", "false"],
    ["6", "Frank", "83.2", "true"],
    ["7", "Grace", "96.7", "true"],
    ["8", "Henry", "74.9", "false"],
]

EXPECTED_JSON = {
    "app": {
        "name": "Alpha",
        "version": "2.1.0",
        "debug": False,
        "port": 8080,
        "database": {
            "host": "localhost",
            "name": "alpha_db",
            "pool_size": 10,
        },
    }
}

EXPECTED_NOTES = (
    "TODO List for Sprint 23\n"
    "---------------------\n"
    "[ ] Fix authentication bug in login module\n"
    "[x] Update dependencies to latest versions\n"
    "[ ] Add rate limiting to API endpoints\n"
    "[x] Refactor database connection pool\n"
    "[ ] Write integration tests for payment flow\n"
    "[ ] Review security audit report\n"
    "[x] Deploy staging environment\n"
    "\n"
    "Blocked: Waiting for QA environment access\n"
)

EXPECTED_SCHEMA_SQL = (
    "CREATE TABLE users (\n"
    "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    username VARCHAR(64) NOT NULL UNIQUE,\n"
    "    email VARCHAR(128) NOT NULL,\n"
    "    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n"
    ");\n"
    "\n"
    "CREATE TABLE orders (\n"
    "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    user_id INTEGER REFERENCES users(id),\n"
    "    total DECIMAL(10,2),\n"
    "    status VARCHAR(16) DEFAULT \"pending\"\n"
    ");\n"
)


def test_recovered_directory_exists():
    """Test that /app/recovered/ directory exists."""
    assert os.path.isdir("/app/recovered"), (
        "/app/recovered/ directory does not exist"
    )


def test_recovered_file_count():
    """Test that exactly 6 files were recovered."""
    files = [
        f for f in os.listdir("/app/recovered/")
        if os.path.isfile(os.path.join("/app/recovered/", f))
    ]
    assert len(files) == 6, (
        f"Expected 6 recovered files, found {len(files)}: {files}"
    )


def test_readme_recovered():
    """Test that readme.txt was recovered with correct content."""
    path = "/app/recovered/readme.txt"
    assert os.path.isfile(path), f"{path} not found"

    with open(path, "r") as f:
        content = f.read()

    assert content == EXPECTED_README, (
        f"readme.txt content mismatch.\n"
        f"Expected ({len(EXPECTED_README)} bytes): {EXPECTED_README!r}\n"
        f"Got ({len(content)} bytes): {content!r}"
    )


def test_csv_recovered():
    """Test that the CSV file was recovered with correct content."""
    recovered_dir = "/app/recovered/"

    csv_path = None
    for fname in os.listdir(recovered_dir):
        fpath = os.path.join(recovered_dir, fname)
        if not os.path.isfile(fpath):
            continue
        with open(fpath, "rb") as f:
            head = f.read(20)
        if b"id,name,score" in head:
            csv_path = fpath
            break

    assert csv_path is not None, (
        "Could not find a recovered file containing CSV header 'id,name,score'"
    )

    with open(csv_path, "r") as f:
        content = f.read()

    lines = content.strip().split("\n")
    assert len(lines) == 9, (
        f"Expected 9 lines (1 header + 8 data rows), got {len(lines)}"
    )
    assert lines[0] == EXPECTED_CSV_HEADER, (
        f"CSV header mismatch: {lines[0]!r}"
    )

    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 9, f"Expected 9 CSV rows, got {len(rows)}"
    assert rows[0] == ["id", "name", "score", "active"], (
        f"CSV header columns mismatch: {rows[0]}"
    )
    for i, expected in enumerate(EXPECTED_CSV_ROWS):
        assert rows[i + 1] == expected, (
            f"CSV row {i + 1} mismatch: expected {expected}, got {rows[i + 1]}"
        )


def test_json_recovered():
    """Test that config.json was recovered with correct content (not extra bytes)."""
    path = "/app/recovered/config.json"
    assert os.path.isfile(path), f"{path} not found"

    with open(path, "rb") as f:
        raw = f.read()

    assert len(raw) == 148, (
        f"config.json should be exactly 148 bytes, got {len(raw)}. "
        f"The corrupted blob has a wrong stated size — "
        f"you may have included trailing garbage from the next entry."
    )

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        assert False, f"config.json is not valid JSON: {e}"

    assert data == EXPECTED_JSON, (
        f"config.json content mismatch.\nExpected: {EXPECTED_JSON}\nGot: {data}"
    )


def test_values_dat_recovered():
    """Test that values.dat was recovered with correct 256 sequential bytes."""
    path = "/app/recovered/values.dat"
    assert os.path.isfile(path), f"{path} not found"

    with open(path, "rb") as f:
        data = f.read()

    assert len(data) == 256, (
        f"values.dat should be exactly 256 bytes, got {len(data)}"
    )

    expected = bytes(range(256))
    assert data == expected, (
        f"values.dat content mismatch. Expected sequential bytes 0x00-0xFF."
    )


def test_notes_recovered():
    """Test that notes.txt was recovered with correct content."""
    path = "/app/recovered/notes.txt"
    assert os.path.isfile(path), f"{path} not found"

    with open(path, "r") as f:
        content = f.read()

    assert content == EXPECTED_NOTES, (
        f"notes.txt content mismatch ({len(content)} vs {len(EXPECTED_NOTES)} bytes)."
    )


def test_schema_sql_recovered():
    """Test that schema.sql was recovered with correct content."""
    path = "/app/recovered/schema.sql"
    assert os.path.isfile(path), f"{path} not found"

    with open(path, "r") as f:
        content = f.read()

    assert content == EXPECTED_SCHEMA_SQL, (
        f"schema.sql content mismatch ({len(content)} vs {len(EXPECTED_SCHEMA_SQL)} bytes)."
    )


def test_no_extra_files():
    """Test that there are exactly 6 recovered files (no extra junk)."""
    files = [
        f for f in os.listdir("/app/recovered/")
        if os.path.isfile(os.path.join("/app/recovered/", f))
    ]
    assert len(files) == 6, (
        f"Expected exactly 6 files in /app/recovered/, got {len(files)}: {files}"
    )
