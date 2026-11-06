A SQLite database at `/app/data.db` had several records accidentally deleted from its `employees` table. The `employees` table uses `INTEGER PRIMARY KEY` for its `id` column (making it an alias for the rowid) and has several additional columns storing employee details.

I need you to write a program that recovers those deleted records directly from the raw database file. The database uses a standard SQLite format with no WAL, no encryption, and default page size. Your recovery target is the supplied database; you do not need to support other database files.

Write a script named `recover.py`. When invoked as `python3 /app/recover.py /app/data.db`, it should output a JSON array of recovered records to stdout. Each object must include a `rowid` key (integer) plus keys for every column of the `employees` table (you'll need to determine the column names from the database itself). The `salary`-equivalent column value must be an integer (not a string); all text columns should be strings.

Records that are only partially recoverable may be omitted — only include records you can fully reconstruct.

Success means your script finds the deleted records that still reside in the database pages and outputs them in valid JSON with correct column names and types.
