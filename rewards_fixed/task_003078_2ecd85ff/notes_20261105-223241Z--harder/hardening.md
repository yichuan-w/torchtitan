# Hardening rationale

## Observed successful strategy and defect

All 12 attempts succeeded. They discover that `/app/embed_oracle` is absent, create an unrelated deterministic replacement, load its bytes into the `docs` table, calculate similarities with NumPy/SciPy, and make `/search` scan every stored embedding in Python. Attempt 01 turns 1-7 (`find`, then `cat > /app/embed_oracle`) and attempt 06 turns 1-4 show the replacement explicitly; their later service code computes every score with NumPy. This is a `reward_hack`: the instruction identifies a supplied proprietary binary whose outputs define the task, but the seed checker invokes whichever solver-written file is left at that path and therefore rewards fabricated embeddings. The environment is also a `broken_setup` because the binary promised by the instruction is absent.

## Primary sources and pinned artifact

- https://raw.githubusercontent.com/asg017/sqlite-vec/v0.1.6/site/features/knn.md — the v0.1.6 project documentation says a `vec0` table uses `distance_metric=cosine` for cosine distance and otherwise identifies L2 as the default in its KNN examples; it also states that `LIMIT` in vec0 KNN queries only works with SQLite 3.41+, while `k = 10` is the compatible form.
- https://raw.githubusercontent.com/asg017/sqlite-vec/v0.1.6/site/using/python.md — the v0.1.6 Python documentation says to call `sqlite_vec.load()` on a SQLite connection and recommends SQLite 3.41 or higher while noting older versions do not support all query forms correctly.
- https://pypi.org/pypi/sqlite-vec/0.1.6/json — PyPI identifies the Linux x86-64 wheel for 0.1.6 and its SHA-256 as `823b0493add80d7fe82ab0fe25df7c0703f4752941aee1c7b2b02cec9656cb24`.

The Dockerfile pins the PyPI `sqlite-vec==0.1.6` Linux wheel by both version and SHA-256. The environment also restores the promised legacy oracle from the already-declared seed implementation as a stripped binary only, preserving the original closed-binary interface rather than exposing a replacement or fix.

## Reproduction in this environment

After installing `sqlite-vec==0.1.6` in the current sandbox, Python reports SQLite 3.37.2 and sqlite-vec v0.1.6. A two-vector vec0 query with query `[1,0]`, stored vectors `[10,0]` and `[1,1]`, and no metric declaration returns `[(2, 1.0), (1, 9.0)]`; the same table declared with `distance_metric=cosine` returns `[(1, 0.0), (2, 0.2928932309150696)]`. Thus the default index silently gives the wrong semantic ranking for this task, while the output still looks like a valid nearest-neighbor result. Reproduction command: create each in-memory vec0 table, insert the two packed float32 vectors, and execute `SELECT id,distance ... WHERE embedding MATCH ? AND k=2`.

## Proposed change and new decision

Preserve the ETL, statistical analysis, API schema, address, and cosine-similarity goal. Require the database to contain and `/search` to query a persistent SQLite vector index. The solver must load the real pinned extension, account for this image's SQLite 3.37 compatibility, deliberately configure cosine distance rather than accept vec0's plausible default L2 ranking, and convert cosine distance to the promised similarity score. This is one modest step beyond the demonstrated SQLite, vector, and endpoint work: it moves the same search operation into a real extension and requires validating its metric semantics.

The environment contains the problem, not its fix: sqlite-vec is installed but no index, schema, service implementation, patch, or note naming the metric choice is shipped. The wrong default is silent and discoverable through extension behavior and upstream docs.

## Shortcut trials and attempt replays

- Suggested/first failure fix: on a fresh reset, `pip3 install --no-deps --force-reinstall sqlite-vec==0.1.6` succeeds and leaves the same version installed. Grading is 0: no database exists, so `test_database_setup` fails first. Reinstallation does not make the deliverable or choose the required metric.
- Plausible default-index fix: after an oracle run, replacing the correct vec0 table with the same populated table using sqlite-vec's default metric and restarting the service grades 0. `test_database_setup` passes; `test_sqlite_vector_index` fails because the schema has no cosine metric. This mirrors the silent L2 result reproduced above rather than announcing a fix.
- Attempt 01 replay: from a fresh revised-image reset, replaying all recorded commands creates 50 `docs` rows and its NumPy service, but its turn 5 overwrites `/app/embed_oracle`. Grading is 0 at the protected-path integrity baseline for `/app/embed_oracle`; every content and API check is therefore withheld. Its old path stops at the fabricated oracle before it ever creates or queries a SQLite vector index.
- Attempt 06 replay: from another fresh reset, all recorded commands likewise create 50 rows, statistics, and a functioning NumPy service, but turn 2 overwrites `/app/embed_oracle`. Grading is 0 at the protected-path integrity baseline for `/app/embed_oracle`; every content and API check is withheld. Its old path also has no SQLite vector index.

The attempt replays fail exactly the protected supplied-oracle requirement and, by inspection of the replayed databases and service source, the new persistent-index and indexed-search requirements. Separately executing the legal brute-force shape without overwriting the binary is represented by the default-index trial: retained database setup passes, but the index metric check rejects the wrong system behavior.

## Predicted next result

5 of 12. The prior student reliably builds ETL, statistics, and Flask service code, but every measured path overwrites the now-protected oracle and scans vectors with NumPy. Capable retries can discover sqlite-vec and its API, while only those that validate the ranking or documentation should select cosine rather than the plausible default L2 metric.
