"""
Simple in-memory SQL database engine skeleton.

Complete the implementation to support:
  CREATE TABLE name (col1 TYPE, col2 TYPE, ...)
  INSERT INTO name VALUES (val1, val2, ...)
  SELECT [DISTINCT] cols FROM name
      [WHERE condition]
      [GROUP BY col]
      [ORDER BY col [ASC|DESC]]
      [LIMIT n]

Supported types: INTEGER, TEXT, REAL

Aggregate functions in SELECT: COUNT(*), COUNT(col), SUM(col), AVG(col)
These work with GROUP BY or on their own (full-table aggregation).

WHERE supports: =, !=, <, >, <=, >=, AND, OR
"""

import re


class Table:
    def __init__(self, name, columns):
        self.name = name
        self.columns = columns    # list of (col_name, col_type)
        self.rows = []


class Database:
    def __init__(self):
        self.tables = {}


class SQLEngine:
    """Parses and executes a subset of SQL.

    Return formats:
      CREATE/INSERT: string message or {"error": "..."}
      SELECT: {"columns": ["col1", ...], "rows": [[val1, ...], ...]}
    """

    def __init__(self):
        self.db = Database()

    def execute(self, sql):
        sql = sql.strip().rstrip(';').strip()
        if not sql:
            return {"error": "empty statement"}
        upper = sql.upper()
        if upper.startswith("CREATE"):
            return self._execute_create(sql)
        elif upper.startswith("INSERT"):
            return self._execute_insert(sql)
        elif upper.startswith("SELECT"):
            return self._execute_select(sql)
        else:
            return {"error": "unknown statement type"}

    def _execute_create(self, sql):
        raise NotImplementedError("CREATE TABLE not implemented")

    def _execute_insert(self, sql):
        raise NotImplementedError("INSERT not implemented")

    def _execute_select(self, sql):
        raise NotImplementedError("SELECT not implemented")
