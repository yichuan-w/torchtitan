import os
import subprocess
from contextlib import contextmanager
from itertools import permutations
from pathlib import Path


SCRIPT = Path("/app/bin/logreport")
LOG = Path("/app/logs/access.log")


def run_report():
    return subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, timeout=10, cwd="/app"
    )


def lines(stdout):
    return [line.strip() for line in stdout.strip().splitlines() if line.strip()]


def expected_reports(total, methods, ips, hours, classes):
    fixed_prefix = [f"total_requests|{total}"]
    fixed_prefix += [f"method|{method}|{count}" for method, count in methods]
    fixed_prefix += [f"top_ip|{ip}|{count}" for ip, count in ips]
    suffix = [f"status_class|{name}|{count}" for name, count in classes]
    sparse_hours = [f"hour|{hour}|{count}" for hour, count in hours]
    all_hours = [f"hour|{hour}|{dict(hours).get(hour, 0)}" for hour in range(24)]
    return [fixed_prefix + sparse_hours + suffix, fixed_prefix + all_hours + suffix]


@contextmanager
def temporary_log(content):
    original = LOG.read_bytes()
    try:
        LOG.write_text(content)
        yield
    finally:
        LOG.write_bytes(original)


def test_script_exists_and_executable():
    assert SCRIPT.is_file() and os.access(SCRIPT, os.X_OK), (
        "/app/bin/logreport must exist and be executable"
    )


def test_supplied_log_report():
    result = run_report()
    assert result.returncode == 0, f"Script failed: {result.stderr[:300]}"

    methods = [("DELETE", 1), ("GET", 15), ("POST", 4), ("PUT", 1)]
    hours = [(8, 2), (9, 2), (10, 3), (11, 2), (12, 2), (13, 2),
             (14, 2), (15, 2), (16, 2), (17, 2), (18, 2), (19, 1)]
    classes = [("2xx", 12), ("3xx", 2), ("4xx", 7), ("5xx", 3)]
    accepted = []
    for leading_tie in (
        [("192.168.1.100", 7), ("10.0.0.50", 7)],
        [("10.0.0.50", 7), ("192.168.1.100", 7)],
    ):
        for fifth_ip in ("8.8.8.8", "198.51.100.27"):
            ips = leading_tie + [("172.16.0.1", 5), ("203.0.113.9", 3), (fifth_ip, 1)]
            accepted.extend(expected_reports(24, methods, ips, hours, classes))
    assert lines(result.stdout) in accepted, f"Incorrect supplied-log report:\n{result.stdout}"


def test_report_tracks_fresh_log_content():
    fresh_log = """192.0.2.1 - - [01/Jul/2026:23:59:59 +0000] \"PATCH /a HTTP/1.1\" 204 0 \"-\" \"agent\"
192.0.2.1 - - [02/Jul/2026:00:00:01 +0000] \"GET /quoted\\\"path HTTP/1.1\" 302 0 \"-\" \"agent\"
192.0.2.2 - - [02/Jul/2026:00:01:00 +0000] \"GET /b HTTP/1.1\" 404 0 \"-\" \"agent\"
192.0.2.3 - - [02/Jul/2026:01:01:00 +0000] \"POST /c HTTP/1.1\" 503 0 \"-\" \"agent\"
192.0.2.4 - - [02/Jul/2026:02:01:00 +0000] \"GET /d HTTP/1.1\" 200 0 \"-\" \"agent\"
192.0.2.5 - - [02/Jul/2026:03:01:00 +0000] \"GET /e HTTP/1.1\" 200 0 \"-\" \"agent\"
192.0.2.6 - - [02/Jul/2026:04:01:00 +0000] \"-\" 408 0 \"-\" \"-\"
"""
    with temporary_log(fresh_log):
        result = run_report()
    expected = []
    tied_ips = ["192.0.2.2", "192.0.2.3", "192.0.2.4", "192.0.2.5", "192.0.2.6"]
    for selected in permutations(tied_ips, 4):
        ips = [("192.0.2.1", 2)] + [(ip, 1) for ip in selected]
        expected.extend(expected_reports(
            7,
            [("GET", 4), ("PATCH", 1), ("POST", 1)],
            ips,
            [(0, 2), (1, 1), (2, 1), (3, 1), (4, 1), (23, 1)],
            [("2xx", 3), ("3xx", 1), ("4xx", 2), ("5xx", 1)],
        ))
    assert result.returncode == 0 and lines(result.stdout) in expected, (
        f"Incorrect fresh-log report (stderr={result.stderr!r}):\n{result.stdout}"
    )


def test_empty_log_report():
    with temporary_log(""):
        result = run_report()
    expected = expected_reports(
        0, [], [], [], [("2xx", 0), ("3xx", 0), ("4xx", 0), ("5xx", 0)]
    )
    assert result.returncode == 0 and lines(result.stdout) in expected, (
        f"Incorrect empty-log report (stderr={result.stderr!r}):\n{result.stdout}"
    )
