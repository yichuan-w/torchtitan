"""
Tests for the SQL database engine.
"""

import sys
sys.path.insert(0, '/app')
from db_engine import SQLEngine


def _run(engine, sql):
    return engine.execute(sql)


def test_create_and_insert():
    engine = SQLEngine()
    r = _run(engine, "CREATE TABLE users (id INTEGER, name TEXT, score REAL)")
    assert "error" not in r
    assert engine.db.tables["users"].columns == [
        ("id", "INTEGER"), ("name", "TEXT"), ("score", "REAL")]
    _run(engine, "INSERT INTO users VALUES (1, 'alice', 95.5)")
    _run(engine, "INSERT INTO users VALUES (2, 'bob', 87.0)")
    assert len(engine.db.tables["users"].rows) == 2


def test_select_basic():
    engine = SQLEngine()
    _run(engine, "CREATE TABLE t (a INTEGER, b TEXT)")
    _run(engine, "INSERT INTO t VALUES (1, 'x')")
    _run(engine, "INSERT INTO t VALUES (2, 'y')")
    r = _run(engine, "SELECT * FROM t")
    assert r["columns"] == ["a", "b"]
    assert r["rows"] == [[1, "x"], [2, "y"]]
    r = _run(engine, "SELECT a FROM t")
    assert r["columns"] == ["a"]
    assert r["rows"] == [[1], [2]]


def test_select_where():
    engine = SQLEngine()
    _run(engine, "CREATE TABLE t (a INTEGER, b TEXT)")
    _run(engine, "INSERT INTO t VALUES (1, 'alice')")
    _run(engine, "INSERT INTO t VALUES (2, 'bob')")
    _run(engine, "INSERT INTO t VALUES (3, 'alice')")
    r = _run(engine, "SELECT a FROM t WHERE b = 'alice'")
    assert r["rows"] == [[1], [3]]
    r = _run(engine, "SELECT * FROM t WHERE a > 1 AND b = 'bob'")
    assert r["rows"] == [[2, "bob"]]
    r = _run(engine, "SELECT * FROM t WHERE a = 1 OR a = 3")
    assert r["rows"] == [[1, "alice"], [3, "alice"]]


def test_select_order_limit():
    engine = SQLEngine()
    _run(engine, "CREATE TABLE t (val INTEGER)")
    for v in [5, 3, 1, 4, 2]:
        _run(engine, f"INSERT INTO t VALUES ({v})")
    r = _run(engine, "SELECT val FROM t ORDER BY val ASC")
    assert r["rows"] == [[1], [2], [3], [4], [5]]
    r = _run(engine, "SELECT val FROM t ORDER BY val DESC LIMIT 3")
    assert r["rows"] == [[5], [4], [3]]


def test_group_by_count():
    engine = SQLEngine()
    _run(engine, "CREATE TABLE sales (region TEXT, amount INTEGER)")
    _run(engine, "INSERT INTO sales VALUES ('East', 100)")
    _run(engine, "INSERT INTO sales VALUES ('West', 200)")
    _run(engine, "INSERT INTO sales VALUES ('East', 150)")
    _run(engine, "INSERT INTO sales VALUES ('West', 300)")
    _run(engine, "INSERT INTO sales VALUES ('East', 50)")
    r = _run(engine, "SELECT region, COUNT(*) FROM sales GROUP BY region")
    assert r["columns"] == ["region", "COUNT(*)"]
    rows = sorted(r["rows"], key=lambda x: x[0])
    assert rows == [["East", 3], ["West", 2]]


def test_group_by_sum_avg():
    engine = SQLEngine()
    _run(engine, "CREATE TABLE t (grp TEXT, val INTEGER)")
    _run(engine, "INSERT INTO t VALUES ('A', 10)")
    _run(engine, "INSERT INTO t VALUES ('A', 20)")
    _run(engine, "INSERT INTO t VALUES ('B', 100)")
    r = _run(engine, "SELECT grp, SUM(val), AVG(val) FROM t GROUP BY grp")
    assert r["columns"] == ["grp", "SUM(val)", "AVG(val)"]
    rows = sorted(r["rows"], key=lambda x: x[0])
    assert rows[0] == ["A", 30, 15.0]
    assert rows[1][0] == "B" and rows[1][1] == 100 and rows[1][2] == 100.0


def test_full_table_aggregation():
    engine = SQLEngine()
    _run(engine, "CREATE TABLE t (x INTEGER)")
    for v in [10, 20, 30]:
        _run(engine, f"INSERT INTO t VALUES ({v})")
    r = _run(engine, "SELECT COUNT(*), SUM(x), AVG(x) FROM t")
    assert r["columns"] == ["COUNT(*)", "SUM(x)", "AVG(x)"]
    assert r["rows"] == [[3, 60, 20.0]]


def test_select_distinct():
    engine = SQLEngine()
    _run(engine, "CREATE TABLE t (color TEXT)")
    _run(engine, "INSERT INTO t VALUES ('red')")
    _run(engine, "INSERT INTO t VALUES ('blue')")
    _run(engine, "INSERT INTO t VALUES ('red')")
    _run(engine, "INSERT INTO t VALUES ('green')")
    _run(engine, "INSERT INTO t VALUES ('blue')")
    r = _run(engine, "SELECT DISTINCT color FROM t ORDER BY color")
    assert r["rows"] == [["blue"], ["green"], ["red"]]


def test_combined():
    engine = SQLEngine()
    _run(engine, "CREATE TABLE orders (dept TEXT, amount INTEGER)")
    _run(engine, "INSERT INTO orders VALUES ('Sales', 500)")
    _run(engine, "INSERT INTO orders VALUES ('Sales', 300)")
    _run(engine, "INSERT INTO orders VALUES ('HR', 100)")
    _run(engine, "INSERT INTO orders VALUES ('Sales', 800)")
    _run(engine, "INSERT INTO orders VALUES ('HR', 200)")
    _run(engine, "INSERT INTO orders VALUES ('IT', 1000)")
    r = _run(engine,
        "SELECT dept, SUM(amount) FROM orders GROUP BY dept "
        "ORDER BY dept ASC LIMIT 2")
    assert r["rows"] == [["HR", 300], ["IT", 1000]]


def test_error_handling():
    engine = SQLEngine()
    assert "error" in _run(engine, "SELECT * FROM nonexistent")
    _run(engine, "CREATE TABLE t (a INTEGER)")
    assert "error" in _run(engine, "CREATE TABLE t (b TEXT)")
    assert "error" in _run(engine, "INSERT INTO t VALUES (1, 2)")
    _run(engine, "INSERT INTO t VALUES (1)")
    assert "error" in _run(engine, "SELECT bad FROM t")
    assert "error" in _run(engine, "SELECT * FROM t GROUP BY nonexistent")
