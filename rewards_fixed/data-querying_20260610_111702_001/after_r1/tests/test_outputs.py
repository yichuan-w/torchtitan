import csv
import io
import subprocess
from pathlib import Path

SOLUTION_PATH = Path("/app/solution.sql")

# Section 1 expected: for each (cve_id, affected_package), the minimum depth
EXPECTED_EXPOSURE = {
    # CVE-2024-0001 (foundation-lib)
    ("CVE-2024-0001", "auth-service"): 1,
    ("CVE-2024-0001", "database-connector"): 1,
    ("CVE-2024-0001", "config-parser"): 1,
    ("CVE-2024-0001", "serialization-lib"): 1,
    ("CVE-2024-0001", "payment-processor"): 1,
    ("CVE-2024-0001", "data-validator"): 1,
    ("CVE-2024-0001", "http-server"): 2,
    ("CVE-2024-0001", "api-gateway"): 2,
    ("CVE-2024-0001", "user-management"): 2,
    ("CVE-2024-0001", "message-queue"): 2,
    ("CVE-2024-0001", "template-engine"): 2,
    ("CVE-2024-0001", "file-uploader"): 2,
    ("CVE-2024-0001", "search-indexer"): 2,
    ("CVE-2024-0001", "backup-service"): 2,
    ("CVE-2024-0001", "notification-dispatcher"): 2,
    ("CVE-2024-0001", "config-watcher"): 2,
    ("CVE-2024-0001", "audit-logger"): 2,
    ("CVE-2024-0001", "event-bus"): 3,
    ("CVE-2024-0001", "load-balancer"): 3,
    ("CVE-2024-0001", "health-checker"): 3,
    ("CVE-2024-0001", "request-tracer"): 3,
    ("CVE-2024-0001", "job-queue"): 3,
    ("CVE-2024-0001", "content-delivery"): 3,
    ("CVE-2024-0001", "circuit-breaker"): 4,
    ("CVE-2024-0001", "service-registry"): 4,
    ("CVE-2024-0001", "scheduler"): 4,
    # CVE-2024-0002 (encryption-lib)
    ("CVE-2024-0002", "auth-service"): 1,
    ("CVE-2024-0002", "payment-processor"): 1,
    ("CVE-2024-0002", "file-uploader"): 1,
    ("CVE-2024-0002", "api-gateway"): 2,
    ("CVE-2024-0002", "user-management"): 2,
    ("CVE-2024-0002", "notification-dispatcher"): 2,
    ("CVE-2024-0002", "audit-logger"): 2,
    # CVE-2024-0003 (config-parser)
    ("CVE-2024-0003", "http-server"): 1,
    ("CVE-2024-0003", "template-engine"): 1,
    ("CVE-2024-0003", "config-watcher"): 1,
    ("CVE-2024-0003", "api-gateway"): 2,
    ("CVE-2024-0003", "file-uploader"): 2,
    ("CVE-2024-0003", "load-balancer"): 2,
    ("CVE-2024-0003", "health-checker"): 2,
    ("CVE-2024-0003", "request-tracer"): 2,
    ("CVE-2024-0003", "content-delivery"): 2,
    ("CVE-2024-0003", "circuit-breaker"): 3,
    ("CVE-2024-0003", "service-registry"): 3,
    # CVE-2024-0004 (serialization-lib)
    ("CVE-2024-0004", "payment-processor"): 1,
    ("CVE-2024-0004", "message-queue"): 1,
    ("CVE-2024-0004", "search-indexer"): 1,
    ("CVE-2024-0004", "event-bus"): 2,
    ("CVE-2024-0004", "backup-service"): 2,
    ("CVE-2024-0004", "request-tracer"): 2,
    ("CVE-2024-0004", "job-queue"): 2,
    ("CVE-2024-0004", "audit-logger"): 2,
    ("CVE-2024-0004", "content-delivery"): 2,
    ("CVE-2024-0004", "notification-dispatcher"): 3,
    ("CVE-2024-0004", "scheduler"): 3,
    # CVE-2024-0005 (core-utils)
    ("CVE-2024-0005", "networking"): 1,
    ("CVE-2024-0005", "http-server"): 1,
    ("CVE-2024-0005", "auth-service"): 1,
    ("CVE-2024-0005", "database-connector"): 1,
    ("CVE-2024-0005", "logging-framework"): 1,
    ("CVE-2024-0005", "config-parser"): 1,
    ("CVE-2024-0005", "serialization-lib"): 1,
    ("CVE-2024-0005", "cache-manager"): 1,
    ("CVE-2024-0005", "monitoring-agent"): 1,
    ("CVE-2024-0005", "data-validator"): 1,
    ("CVE-2024-0005", "template-engine"): 1,
    ("CVE-2024-0005", "rate-limiter"): 1,
    ("CVE-2024-0005", "search-indexer"): 1,
    ("CVE-2024-0005", "metrics-collector"): 1,
    ("CVE-2024-0005", "api-gateway"): 2,
    ("CVE-2024-0005", "user-management"): 2,
    ("CVE-2024-0005", "payment-processor"): 2,
    ("CVE-2024-0005", "message-queue"): 2,
    ("CVE-2024-0005", "file-uploader"): 2,
    ("CVE-2024-0005", "event-bus"): 2,
    ("CVE-2024-0005", "backup-service"): 2,
    ("CVE-2024-0005", "notification-dispatcher"): 2,
    ("CVE-2024-0005", "load-balancer"): 2,
    ("CVE-2024-0005", "circuit-breaker"): 2,
    ("CVE-2024-0005", "health-checker"): 2,
    ("CVE-2024-0005", "request-tracer"): 2,
    ("CVE-2024-0005", "config-watcher"): 2,
    ("CVE-2024-0005", "scheduler"): 2,
    ("CVE-2024-0005", "audit-logger"): 2,
    ("CVE-2024-0005", "content-delivery"): 2,
    ("CVE-2024-0005", "service-registry"): 3,
    ("CVE-2024-0005", "job-queue"): 3,
}

# Section 2 expected: packages exposed to 2+ CVEs, with count
EXPECTED_MULTI_HIT = {
    "api-gateway": 4,
    "audit-logger": 4,
    "content-delivery": 4,
    "file-uploader": 4,
    "notification-dispatcher": 4,
    "payment-processor": 4,
    "request-tracer": 4,
    "auth-service": 3,
    "backup-service": 3,
    "circuit-breaker": 3,
    "config-watcher": 3,
    "event-bus": 3,
    "health-checker": 3,
    "http-server": 3,
    "job-queue": 3,
    "load-balancer": 3,
    "message-queue": 3,
    "scheduler": 3,
    "search-indexer": 3,
    "service-registry": 3,
    "template-engine": 3,
    "user-management": 3,
    "config-parser": 2,
    "data-validator": 2,
    "database-connector": 2,
    "serialization-lib": 2,
}

EXPECTED_VULNERABLE_PACKAGE = {
    "CVE-2024-0001": "foundation-lib",
    "CVE-2024-0002": "encryption-lib",
    "CVE-2024-0003": "config-parser",
    "CVE-2024-0004": "serialization-lib",
    "CVE-2024-0005": "core-utils",
}

PACKAGE_NAMES = {
    1: "foundation-lib", 2: "core-utils", 3: "networking",
    4: "http-server", 5: "auth-service", 6: "database-connector",
    7: "logging-framework", 8: "config-parser", 9: "serialization-lib",
    10: "api-gateway", 11: "user-management", 12: "payment-processor",
    13: "cache-manager", 14: "message-queue", 15: "monitoring-agent",
    16: "data-validator", 17: "encryption-lib", 18: "template-engine",
    19: "file-uploader", 20: "rate-limiter", 21: "search-indexer",
    22: "event-bus", 23: "metrics-collector", 24: "backup-service",
    25: "notification-dispatcher", 26: "load-balancer",
    27: "circuit-breaker", 28: "service-registry", 29: "health-checker",
    30: "request-tracer", 31: "config-watcher", 32: "scheduler",
    33: "job-queue", 34: "audit-logger", 35: "content-delivery",
}

DEPENDENCY_IDS = {
    (3, 2), (3, 7), (7, 2), (7, 3), (8, 2), (8, 1), (9, 2), (9, 1),
    (13, 2), (13, 20), (16, 2), (16, 1), (20, 2), (20, 13), (18, 2),
    (18, 8), (4, 3), (4, 2), (4, 8), (6, 2), (6, 1), (14, 3), (14, 9),
    (5, 1), (5, 17), (5, 2), (10, 4), (10, 5), (10, 20), (10, 13),
    (12, 1), (12, 6), (12, 17), (12, 9), (11, 5), (11, 6), (11, 16),
    (15, 7), (15, 2), (19, 4), (19, 16), (19, 17), (21, 2), (21, 9),
    (22, 3), (22, 14), (23, 15), (23, 2), (24, 6), (24, 21), (25, 22),
    (25, 5), (26, 4), (26, 27), (27, 26), (27, 23), (28, 26), (28, 27),
    (29, 4), (29, 28), (30, 4), (30, 21), (31, 8), (31, 28), (32, 33),
    (32, 13), (33, 14), (33, 22), (34, 21), (34, 5), (35, 4), (35, 13),
    (35, 21),
}

DEPENDENCY_EDGES = {
    (PACKAGE_NAMES[package_id], PACKAGE_NAMES[depends_on_id])
    for package_id, depends_on_id in DEPENDENCY_IDS
}


def _run_query():
    """Execute the solution query and return parsed rows."""
    query = SOLUTION_PATH.read_text()
    result = subprocess.run(
        ["duckdb", "-csv", "-c", query],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"DuckDB query failed with exit code {result.returncode}.\n"
        f"stderr: {result.stderr}"
    )
    reader = csv.DictReader(io.StringIO(result.stdout))
    return list(reader), list(reader.fieldnames or [])


def _is_null(value):
    return value.strip().upper() in {"", "NULL"}


def test_solution_file_exists():
    """Verify the solution SQL file exists."""
    assert SOLUTION_PATH.exists(), (
        f"Solution file {SOLUTION_PATH} does not exist. "
        "Create /app/solution.sql with your DuckDB query."
    )


def test_query_executes_without_error():
    """Verify the SQL query runs successfully in DuckDB."""
    _run_query()


def test_query_has_required_columns():
    """Verify the revised shared schema and section-specific NULL fields."""
    rows, cols = _run_query()

    expected_columns = [
        "section", "cve_id", "vulnerable_package", "affected_package",
        "depth", "path", "package_name", "cve_count",
    ]
    assert cols == expected_columns, (
        f"Expected shared column order {expected_columns}, found {cols}"
    )

    sections = {row["section"].strip() for row in rows}
    assert "exposure" in sections, (
        f"Missing 'exposure' section in output. Found sections: {sections}"
    )
    assert "multi_hit" in sections, (
        f"Missing 'multi_hit' section in output. Found sections: {sections}"
    )

    for row in rows:
        if row["section"].strip() == "exposure":
            assert _is_null(row["package_name"]) and _is_null(row["cve_count"]), (
                f"exposure row must have NULL package_name and cve_count: {row}"
            )
        elif row["section"].strip() == "multi_hit":
            null_fields = [
                "cve_id", "vulnerable_package", "affected_package", "depth", "path"
            ]
            assert all(_is_null(row[field]) for field in null_fields), (
                f"multi_hit row must have NULL exposure fields: {row}"
            )


def test_exposure_section_results():
    """Verify Section 1 (exposure) returns correct shortest-path results."""
    rows, cols = _run_query()
    exposure_rows = [r for r in rows if r["section"].strip() == "exposure"]

    assert len(exposure_rows) > 0, "No rows found in 'exposure' section"

    required = {"cve_id", "vulnerable_package", "affected_package", "depth", "path"}
    missing = required - set(cols)
    assert not missing, f"Missing columns in output: {missing}"

    # Build results dict: (cve_id, affected_package) -> min depth
    results = {}
    for row in exposure_rows:
        key = (row["cve_id"].strip(), row["affected_package"].strip())
        try:
            depth = int(row["depth"].strip())
        except (ValueError, KeyError):
            assert False, f"Invalid or missing depth value in row: {row}"
        assert key not in results, f"More than one exposure path returned for {key}"

        vulnerable_package = row["vulnerable_package"].strip()
        assert vulnerable_package == EXPECTED_VULNERABLE_PACKAGE.get(key[0]), (
            f"Wrong vulnerable package for {key[0]}: {vulnerable_package}"
        )

        path = row.get("path", "").strip()
        path_packages = [package.strip() for package in path.split("->")]
        assert path_packages[0] == vulnerable_package and path_packages[-1] == key[1], (
            f"Path endpoints do not match exposure row: {row}"
        )
        assert len(path_packages) - 1 == depth, (
            f"Path length does not match depth in row: {row}"
        )
        assert all(
            (dependent, dependency) in DEPENDENCY_EDGES
            for dependency, dependent in zip(path_packages, path_packages[1:])
        ), f"Path contains a non-dependency edge: {row}"

        results[key] = (depth, path)

    # Verify expected results
    missing_results = []
    wrong_depth = []
    for (cve, pkg), expected_depth in EXPECTED_EXPOSURE.items():
        if (cve, pkg) not in results:
            missing_results.append(f"  {cve} -> {pkg} (expected depth {expected_depth})")
        elif results[(cve, pkg)][0] != expected_depth:
            wrong_depth.append(
                f"  {cve} -> {pkg}: expected depth {expected_depth}, "
                f"got {results[(cve, pkg)][0]}"
            )

    extra_results = []
    for (cve, pkg), (depth, _) in results.items():
        if (cve, pkg) not in EXPECTED_EXPOSURE:
            extra_results.append(f"  {cve} -> {pkg} (depth {depth})")

    errors = []
    if missing_results:
        errors.append("Missing expected exposure results:\n" + "\n".join(missing_results))
    if wrong_depth:
        errors.append("Wrong dependency depths:\n" + "\n".join(wrong_depth))
    if extra_results:
        errors.append("Unexpected extra exposure results:\n" + "\n".join(extra_results))
    if errors:
        assert False, "\n\n".join(errors)


def test_multi_hit_section_results():
    """Verify Section 2 (multi_hit) returns correct multi-vulnerability packages."""
    rows, _ = _run_query()
    multi_rows = [r for r in rows if r["section"].strip() == "multi_hit"]

    assert len(multi_rows) > 0, "No rows found in 'multi_hit' section"

    results = {}
    for row in multi_rows:
        pkg = row["package_name"].strip()
        try:
            count = int(row["cve_count"].strip())
        except (ValueError, KeyError):
            assert False, f"Invalid or missing cve_count in row: {row}"
        results[pkg] = count

    # Verify all expected packages present with correct counts
    for pkg, expected_count in EXPECTED_MULTI_HIT.items():
        assert pkg in results, (
            f"Missing package '{pkg}' in multi_hit section "
            f"(expected count {expected_count})"
        )
        assert results[pkg] == expected_count, (
            f"Wrong CVE count for '{pkg}': expected {expected_count}, "
            f"got {results[pkg]}"
        )

    # Verify no extra packages
    for pkg in results:
        assert pkg in EXPECTED_MULTI_HIT, (
            f"Unexpected package '{pkg}' in multi_hit section "
            f"(count {results[pkg]})"
        )

    # Verify ordering: descending by count, then alphabetically
    prev_count = float("inf")
    prev_name = ""
    for row in multi_rows:
        pkg = row["package_name"].strip()
        count = int(row["cve_count"].strip())
        if count > prev_count:
            assert False, (
                f"multi_hit not sorted by cve_count descending: "
                f"'{prev_name}' ({prev_count}) before '{pkg}' ({count})"
            )
        if count == prev_count and pkg < prev_name:
            assert False, (
                f"multi_hit not sorted alphabetically for equal counts: "
                f"'{prev_name}' before '{pkg}'"
            )
        prev_count = count
        prev_name = pkg


def test_query_handles_cycles():
    """Verify the query completes within timeout (no infinite loops from cycles)."""
    query = SOLUTION_PATH.read_text()
    result = subprocess.run(
        ["duckdb", "-csv", "-c", query],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (
        f"Query may have an infinite loop or cycle issue.\n"
        f"stderr: {result.stderr}"
    )
