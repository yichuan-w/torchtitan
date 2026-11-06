"""Verify recovery of corrupted SQLite databases.

Tests check that the solver has:
1. Produced /app/recovered.sql
2. The SQL dump imports cleanly into a fresh database
3. The recovered database contains the expected schema and data
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RECOVERED_SQL = Path("/app/recovered.sql")


def _fresh_import(sql_path: Path) -> sqlite3.Connection:
    """Import the recovered SQL into a fresh in-memory database and return the connection."""
    conn = sqlite3.connect(":memory:")
    with open(sql_path, "r", encoding="utf-8", errors="replace") as f:
        sql_text = f.read()
    conn.executescript(sql_text)
    conn.commit()
    return conn


def test_recovered_sql_exists():
    """The recovered SQL dump must exist and be non-empty."""
    assert RECOVERED_SQL.exists(), f"Missing recovered SQL file: {RECOVERED_SQL}"
    size = RECOVERED_SQL.stat().st_size
    assert size > 100, f"Recovered SQL file too small ({size} bytes); expected a full dump"


def test_recovered_sql_imports_cleanly():
    """The recovered SQL must import into a fresh database without errors."""
    try:
        conn = _fresh_import(RECOVERED_SQL)
        conn.close()
    except Exception as e:
        raise AssertionError(f"Failed to import recovered SQL: {e}")


def test_users_table_complete():
    """All 5 users must be recovered with correct data."""
    conn = _fresh_import(RECOVERED_SQL)
    cur = conn.execute("SELECT id, name, email FROM users ORDER BY id")
    rows = cur.fetchall()
    conn.close()

    expected = [
        (1, "Alice Johnson", "alice@example.com"),
        (2, "Bob Smith", "bob@example.com"),
        (3, "Charlie Brown", "charlie@example.com"),
        (4, "Diana Prince", "diana@example.com"),
        (5, "Evan Wright", "evan@example.com"),
    ]
    assert len(rows) == 5, f"Expected 5 users, got {len(rows)}: {rows}"
    for i, (exp, got) in enumerate(zip(expected, rows)):
        assert got == exp, f"User row {i} mismatch: expected {exp}, got {got}"


def test_products_table_complete():
    """All 6 products must be recovered with correct data."""
    conn = _fresh_import(RECOVERED_SQL)
    cur = conn.execute("SELECT id, sku, name, category, stock FROM products ORDER BY id")
    rows = cur.fetchall()
    conn.close()

    expected = [
        (1, "WDG-001", "Blue Widget", "Widgets", 150),
        (2, "WDG-002", "Red Widget", "Widgets", 200),
        (3, "GZM-001", "Standard Gizmo", "Gizmos", 75),
        (4, "GZM-002", "Deluxe Gizmo", "Gizmos", 30),
        (5, "DBT-001", "Thingamabob", "Doohickeys", 500),
        (6, "DBT-002", "Whatchamacallit", "Doohickeys", 120),
    ]
    assert len(rows) == 6, f"Expected 6 products, got {len(rows)}: {rows}"
    for i, (exp, got) in enumerate(zip(expected, rows)):
        assert got == exp, f"Product row {i} mismatch: expected {exp}, got {got}"


def test_orders_table_complete():
    """All 10 orders must be recovered with correct data."""
    conn = _fresh_import(RECOVERED_SQL)
    cur = conn.execute(
        "SELECT id, user_id, product, quantity, price FROM orders ORDER BY id"
    )
    rows = cur.fetchall()
    conn.close()

    expected = [
        (1, 1, "Blue Widget", 5, 9.99),
        (2, 1, "Standard Gizmo", 2, 24.99),
        (3, 2, "Red Widget", 10, 9.99),
        (4, 3, "Deluxe Gizmo", 1, 49.99),
        (5, 4, "Thingamabob", 3, 5.99),
        (6, 5, "Blue Widget", 8, 9.99),
        (7, 5, "Whatchamacallit", 2, 15.99),
        (8, 2, "Standard Gizmo", 4, 24.99),
        (9, 3, "Thingamabob", 12, 5.99),
        (10, 4, "Red Widget", 6, 9.99),
    ]
    assert len(rows) == 10, f"Expected 10 orders, got {len(rows)}: {rows}"
    for i, (exp, got) in enumerate(zip(expected, rows)):
        assert got == exp, f"Order row {i} mismatch: expected {exp}, got {got}"


def test_data_db_header_repaired():
    """The original data.db must have its own page size restored and open."""
    data_db = Path("/app/data.db")
    assert data_db.exists(), "Missing required repaired database: /app/data.db"

    with open(data_db, "rb") as f:
        page_size_bytes = f.read(18)[16:18]

    page_size = int.from_bytes(page_size_bytes, "big")
    assert page_size == 4096, (
        f"data.db page size is {page_size}, expected 4096. "
        f"Raw bytes at offset 16: {page_size_bytes.hex()}"
    )

    try:
        conn = sqlite3.connect(f"file:{data_db}?mode=ro", uri=True)
        conn.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchall()
        conn.close()
    except sqlite3.DatabaseError as exc:
        raise AssertionError(f"Repaired /app/data.db does not open normally: {exc}")
