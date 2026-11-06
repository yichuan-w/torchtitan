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
    return list(reader), set(reader.fieldnames or [])


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
    """Verify the query output includes the section column plus expected columns."""
    rows, cols = _run_query()

    assert "section" in cols, (
        f"Query output is missing the 'section' column. Found columns: {cols}"
    )

    sections = {row["section"].strip() for row in rows}
    assert "exposure" in sections, (
        f"Missing 'exposure' section in output. Found sections: {sections}"
    )
    assert "multi_hit" in sections, (
        f"Missing 'multi_hit' section in output. Found sections: {sections}"
    )


def test_exposure_section_results():
    """Verify Section 1 (exposure) returns correct shortest-path results."""
    rows, cols = _run_query()
    exposure_rows = [r for r in rows if r["section"].strip() == "exposure"]

    assert len(exposure_rows) > 0, "No rows found in 'exposure' section"

    required = {"cve_id", "vulnerable_package", "affected_package", "depth", "path"}
    missing = required - cols
    assert not missing, f"Missing columns in output: {missing}"

    # Build results dict: (cve_id, affected_package) -> min depth
    results = {}
    for row in exposure_rows:
        key = (row["cve_id"].strip(), row["affected_package"].strip())
        try:
            depth = int(row["depth"].strip())
        except (ValueError, KeyError):
            assert False, f"Invalid or missing depth value in row: {row}"
        if key not in results or depth < results[key][0]:
            results[key] = (depth, row.get("path", "").strip())

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