A security audit of our internal package registry has identified five packages with known vulnerabilities listed in `/app/vulnerabilities.csv`. The dependency graph is spread across `/app/packages.csv` and `/app/dependencies.csv`.

Use DuckDB (pre-installed) to write a single SQL query saved to `/app/solution.sql` that produces a vulnerability impact report with **two sections** combined via `UNION ALL`:

**Section 1 — Transitive Exposure (label: `'exposure'`)**

For each CVE, list every package that transitively depends on the vulnerable package. For each affected package, show only the **shortest** dependency path (minimum number of hops). The dependency graph contains cycles — your query must handle them without looping indefinitely and must correctly compute shortest paths.

In the combined result, use this shared column order: `section`, `cve_id`, `vulnerable_package`, `affected_package`, `depth`, `path`, `package_name`, `cve_count`. For `exposure` rows, set `package_name` and `cve_count` to `NULL`.

**Section 2 — Multi-Vulnerability Packages (label: `'multi_hit'`)**

Identify packages that are transitively exposed to **two or more distinct CVEs** (through any dependency chain). For each such package, list the package name and the count of distinct CVEs it's exposed to. Order by CVE count descending, then alphabetically by package name.

For `multi_hit` rows, put the package and count in `package_name` and `cve_count`, and set `cve_id`, `vulnerable_package`, `affected_package`, `depth`, and `path` to `NULL`.

The `path` column in Section 1 should be the dependency chain as a `->`-separated string (e.g., `foundation-lib -> config-parser -> http-server`).
