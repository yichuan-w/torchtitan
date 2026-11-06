"""Tests for SQLite deleted record recovery task."""
import json
import os
import subprocess
import sqlite3


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
    """Run recover.py on /app/data.db and verify all 12 deleted records are found."""
    records = _run_recovery("/app/data.db")

    # The 12 deleted records (ids: 5, 23, 47, 72, 98, 123, 156, 189, 201, 234, 267, 288)
    expected = {
        5: {
            "rowid": 5, "name": "Jake Turner", "department": "Operations",
            "salary": 54165, "hire_date": "2015-02-07", "email": "jake.turner5@corp.com",
        },
        23: {
            "rowid": 23, "name": "Uma Liu", "department": "Design",
            "salary": 72431, "hire_date": "2023-12-08", "email": "uma.liu23@corp.com",
        },
        47: {
            "rowid": 47, "name": "Alice Wong", "department": "Design",
            "salary": 149943, "hire_date": "2017-09-25", "email": "alice.wong47@corp.com",
        },
        72: {
            "rowid": 72, "name": "Ivy Singh", "department": "Engineering",
            "salary": 68373, "hire_date": "2021-03-09", "email": "ivy.singh72@corp.com",
        },
        98: {
            "rowid": 98, "name": "Chloe Park", "department": "Legal",
            "salary": 87353, "hire_date": "2017-08-27", "email": "chloe.park98@corp.com",
        },
        123: {
            "rowid": 123, "name": "Paul Brown", "department": "Marketing",
            "salary": 110332, "hire_date": "2020-05-27", "email": "paul.brown123@corp.com",
        },
        156: {
            "rowid": 156, "name": "Leo Singh", "department": "Finance",
            "salary": 105724, "hire_date": "2018-03-26", "email": "leo.singh156@corp.com",
        },
        189: {
            "rowid": 189, "name": "Henry Rossi", "department": "Operations",
            "salary": 79389, "hire_date": "2016-11-02", "email": "henry.rossi189@corp.com",
        },
        201: {
            "rowid": 201, "name": "Steve Turner", "department": "HR",
            "salary": 136706, "hire_date": "2020-02-17", "email": "steve.turner201@corp.com",
        },
        234: {
            "rowid": 234, "name": "Olga Kwon", "department": "Legal",
            "salary": 141411, "hire_date": "2017-11-28", "email": "olga.kwon234@corp.com",
        },
        267: {
            "rowid": 267, "name": "Kara Miller", "department": "Marketing",
            "salary": 118021, "hire_date": "2022-05-06", "email": "kara.miller267@corp.com",
        },
        288: {
            "rowid": 288, "name": "Quincy Mendez", "department": "Engineering",
            "salary": 113242, "hire_date": "2022-04-15", "email": "quincy.mendez288@corp.com",
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
    expected_deleted = {5, 23, 47, 72, 98, 123, 156, 189, 201, 234, 267, 288}
    for rec in records:
        rid = rec["rowid"]
        assert rid in expected_deleted, (
            f"Record with rowid {rid} is a live record, not deleted."
        )


def test_recovers_from_second_database():
    """Verify recover.py generalizes: create a different DB (same schema, different
    data, enough records to also have interior pages), delete records, recover them."""
    import random
    random.seed(99)

    test_db = "/tmp/test_recover.db"
    if os.path.exists(test_db):
        os.remove(test_db)

    conn = sqlite3.connect(test_db)
    conn.execute("PRAGMA secure_delete = OFF")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("""
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY,
            name TEXT,
            department TEXT,
            salary INTEGER,
            hire_date TEXT,
            email TEXT
        )
    """)

    depts = ["Engineering", "Sales", "HR", "Finance", "Design"]
    names = [("Anna", "K"), ("Ben", "L"), ("Clara", "M"), ("David", "N"),
             ("Elena", "O"), ("Felix", "P"), ("Gina", "Q"), ("Hugo", "R"),
             ("Iris", "S"), ("Jake", "T"), ("Kara", "U"), ("Liam", "V"),
             ("Maya", "W"), ("Noah", "X")]
    test_data = []
    for i in range(1, 201):
        fn, ln = names[(i * 3) % len(names)]
        dept = depts[(i * 7) % len(depts)]
        salary = random.randint(50000, 120000)
        hire_date = f"2020-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
        email = f"{fn.lower()}.{ln.lower()}{i}@test.org"
        test_data.append((i, f"{fn} {ln}", dept, salary, hire_date, email))

    for row in test_data:
        conn.execute("INSERT INTO employees VALUES (?,?,?,?,?,?)", row)
    conn.commit()

    # Delete two records
    conn.execute("DELETE FROM employees WHERE id IN (7, 42)")
    conn.commit()
    conn.close()

    records = _run_recovery(test_db)

    # Should find rowids 7 and 42
    found_ids = {rec["rowid"] for rec in records}
    assert 7 in found_ids, f"Expected to recover rowid 7, found: {found_ids}"
    assert 42 in found_ids, f"Expected to recover rowid 42, found: {found_ids}"

    # Verify content matches what was inserted
    for rec in records:
        assert isinstance(rec["salary"], int), f"salary not int for rowid {rec.get('rowid')}"
        assert rec["rowid"] in {7, 42}, f"Unexpected rowid recovered: {rec['rowid']}"

    # Ensure no live records
    for rec in records:
        assert rec["rowid"] in {7, 42}, f"Live record {rec['rowid']} falsely recovered"

    os.remove(test_db)


def test_output_is_valid_json_and_has_correct_types():
    """Verify salary is integer and all fields have correct types."""
    records = _run_recovery("/app/data.db")

    required_keys = {"rowid", "name", "department", "salary", "hire_date", "email"}
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
