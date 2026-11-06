Implement a simple in-memory SQL database engine by completing the skeleton in `/app/db_engine.py`.

The engine must support:
- `CREATE TABLE name (col1 TYPE, col2 TYPE, ...)` — types: INTEGER, TEXT, REAL
- `INSERT INTO name VALUES (val1, val2, ...)`
- `SELECT [DISTINCT] cols FROM name [WHERE condition] [GROUP BY col] [ORDER BY col [ASC|DESC]] [LIMIT n]`

WHERE supports: `=`, `!=`, `<`, `>`, `<=`, `>=`, `AND`, `OR`. SELECT supports `*` for all columns.
SELECT also supports `COUNT(*)`, `COUNT(col)`, `SUM(col)`, and `AVG(col)`, both with `GROUP BY` and as full-table aggregates.
Pay particular attention to keeping grouped and ungrouped aggregate queries consistent with the other optional SELECT clauses.

Return formats:
- CREATE/INSERT: a message string on success, `{"error": "..."}` on failure
- SELECT: `{"columns": ["col1", ...], "rows": [[val1, ...], ...]}`

Errors to handle: duplicate tables, unknown tables, wrong column count in INSERT, unknown columns in SELECT (including its optional clauses).

The skeleton provides `Table`, `Database`, and `SQLEngine` classes with the expected return format documented.




