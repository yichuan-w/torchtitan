"""Tests for SQLite deleted record recovery task."""
import json
import os
import subprocess


def _run_recovery(db_path):
    """Run the solver's recover.py on a database and return parsed JSON output."""
    result = subprocess.run(
        ["python3", "/app/recover.py", db_path],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"recover.py exited with code {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(
            f"recover.py did not output valid JSON.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        ) from e
    assert isinstance(data, list), (
        f"Expected a JSON array, got {type(data).__name__}"
    )
    return data


def test_recover_script_exists():
    """Verify recover.py exists and is executable."""
    path = "/app/recover.py"
    assert os.path.exists(path), f"{path} does not exist"
    assert os.path.isfile(path), f"{path} is not a file"


def test_recovers_deleted_records_from_main_db():
    """Run recover.py on /app/data.db and verify all deleted records are found."""
    records = _run_recovery("/app/data.db")

    # The deleted records in the supplied database (ids: 5, 23, 47)
    expected = {
        5: {
            "rowid": 5, "id": 5, "name": "Jake Turner", "department": "Operations",
            "salary": 54165, "hire_date": "2015-02-07", "email": "jake.turner5@corp.com",
        },
        23: {
            "rowid": 23, "id": 23, "name": "Uma Liu", "department": "Design",
            "salary": 72431, "hire_date": "2023-12-08", "email": "uma.liu23@corp.com",
        },
        47: {
            "rowid": 47, "id": 47, "name": "Alice Wong", "department": "Design",
            "salary": 149943, "hire_date": "2017-09-25", "email": "alice.wong47@corp.com",
        },
    }

    found = {rec["rowid"]: rec for rec in records}

    missing = []
    mismatched = []

    for rid, exp in expected.items():
        if rid not in found:
            missing.append(rid)
            continue
        rec = found[rid]
        for key, exp_val in exp.items():
            if key not in rec:
                mismatched.append(f"rowid {rid}: missing key '{key}'")
            elif rec[key] != exp_val:
                mismatched.append(
                    f"rowid {rid}.{key}: expected {exp_val!r}, got {rec[key]!r}"
                )

    assert len(missing) == 0, (
        f"Missing deleted records for rowids: {missing}. "
        f"Found rowids: {sorted(found.keys())}"
    )
    assert len(mismatched) == 0, (
        f"Mismatched record fields:\n" + "\n".join(mismatched)
    )

    # Ensure we don't falsely report live records as deleted
    expected_deleted = {5, 23, 47}
    for rec in records:
        rid = rec["rowid"]
        assert rid in expected_deleted, (
            f"Record with rowid {rid} is a live record, not deleted."
        )


def test_output_is_valid_json_and_has_correct_types():
    """Verify salary is integer and all fields have correct types."""
    records = _run_recovery("/app/data.db")

    required_keys = {"rowid", "id", "name", "department", "salary", "hire_date", "email"}
    for rec in records:
        missing = required_keys - set(rec.keys())
        assert len(missing) == 0, (
            f"Record {rec.get('rowid', '?')} missing keys: {missing}"
        )
        assert isinstance(rec["rowid"], int), (
            f"rowid should be int, got {type(rec['rowid']).__name__}"
        )
        assert isinstance(rec["salary"], int), (
            f"salary should be int, got {type(rec['salary']).__name__}"
        )
        assert isinstance(rec["name"], str), (
            f"name should be str, got {type(rec['name']).__name__}"
        )
        assert isinstance(rec["department"], str), (
            f"department should be str, got {type(rec['department']).__name__}"
        )
        assert isinstance(rec["hire_date"], str), (
            f"hire_date should be str, got {type(rec['hire_date']).__name__}"
        )
        assert isinstance(rec["email"], str), (
            f"email should be str, got {type(rec['email']).__name__}"
        )
