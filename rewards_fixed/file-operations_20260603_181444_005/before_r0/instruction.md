Two SQLite database files in `/app` — `data.db` and `reference.db` — were damaged during a botched filesystem migration. The production data inside `data.db` is believed to be intact, but the file can no longer be opened by the `sqlite3` CLI.

`data.db` and `reference.db` were created by the same application with identical schemas. Their binary headers were corrupted differently:

- `data.db` contains the production data. Multiple fields in its 100‑byte header are damaged and the file is completely unopenable.
- `reference.db` is a near‑empty sister database. It opens correctly in `sqlite3` and its header is intact, but it was configured with different internal parameters than `data.db` — you cannot safely copy its entire header.

Recover all production data from `data.db` by repairing its header at the binary level, then export a complete SQL dump to `/app/recovered.sql`. The dump must include the full schema (CREATE TABLE statements) and all row data (INSERT statements) for every table — exactly what `sqlite3 <db> .dump` would produce from a healthy database. The exported file must be re‑importable into a fresh SQLite database without errors.

The only guaranteed‑available tools in the environment are: `sqlite3`, `xxd`, `dd`, `hexdump`, `od`, `printf`, `cat`, `head`, `tail`, and standard coreutils. You may also write and execute scripts in any language whose runtime is available (Python, shell, etc.).
