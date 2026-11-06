import subprocess
import os
import time
import json
import ssl
import hashlib
import tempfile


def _run(cmd, timeout=15):
    """Run a shell command and return stdout, stderr, returncode."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout, result.stderr, result.returncode


def test_rsyslog_tls_listener():
    """Verify rsyslog is listening on TCP port 6514 and accepts TLS connections."""
    # First check that port 6514 is open
    out, err, rc = _run("ss -tlnp | grep ':6514' || netstat -tlnp 2>/dev/null | grep ':6514'")
    assert rc == 0, (
        f"Port 6514 is not listening. Expected rsyslog TLS listener on port 6514.\n"
        f"ss/netstat output: {out}\nStderr: {err}"
    )

    # Verify TLS handshake works using openssl s_client
    out, err, rc = _run(
        "echo '' | openssl s_client -connect localhost:6514 -brief 2>&1",
        timeout=10,
    )
    # -brief flag outputs "CONNECTION ESTABLISHED" on success or error message
    connected = "CONNECTION ESTABLISHED" in out or "CONNECTION ESTABLISHED" in err
    # Also check for successful verification (we don't verify the cert, just check TLS works)
    has_tls = "Protocol version" in out or "Protocol version" in err or "TLSv" in out or "TLSv" in err
    assert connected or has_tls, (
        f"TLS connection to port 6514 failed. The rsyslog TLS listener is not working correctly.\n"
        f"stdout: {out}\nstderr: {err}"
    )


def test_tls_listener_uses_prepared_localhost_certificate():
    """Verify the live listener presents the prepared localhost certificate."""
    presented_pem = ssl.get_server_certificate(("localhost", 6514))
    presented_der = ssl.PEM_cert_to_DER_cert(presented_pem)
    presented_fingerprint = hashlib.sha256(presented_der).hexdigest()
    prepared_fingerprint = "6f1eeedffa4111be9f118c08b8d305109c877b72948dd579f8f69cb5eec208be"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".crt") as cert_file:
        cert_file.write(presented_pem)
        cert_file.flush()
        decoded = ssl._ssl._test_decode_cert(cert_file.name)
    common_names = [
        value
        for name_group in decoded["subject"]
        for name, value in name_group
        if name == "commonName"
    ]
    assert presented_fingerprint == prepared_fingerprint and "localhost" in common_names, (
        "The TLS listener must present the prepared server certificate whose CN is localhost; "
        "the prepared copy in either stated certificate location is accepted."
    )


def test_log_forwarding():
    """Verify that local syslog messages are forwarded via TLS and appear in remote.log."""
    # Clear remote.log first so we can detect new messages
    _run("truncate -s 0 /var/log/remote.log 2>/dev/null || echo -n > /var/log/remote.log")

    # Send test messages using logger with different facilities and severities
    facilities = ["kern", "auth", "daemon", "syslog", "user", "local0", "local3", "local7"]
    severities = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]

    for i, (fac, sev) in enumerate(zip(facilities, severities)):
        _run(f"logger -p {fac}.{sev} 'TEST_MSG_{i}: facility={fac} severity={sev}'")

    # Wait for messages to be forwarded and processed
    time.sleep(2)

    # Check remote.log for the test messages
    out, err, rc = _run("cat /var/log/remote.log 2>/dev/null")
    content = out + err

    for i in range(len(facilities)):
        assert f"TEST_MSG_{i}" in content, (
            f"Test message TEST_MSG_{i} not found in /var/log/remote.log.\n"
            f"This means log forwarding to the TLS receiver is not working.\n"
            f"Content of remote.log: {content[:2000]}"
        )


def test_analyzer_script_exists():
    """Verify the analyzer script exists at /app/analyze.py."""
    assert os.path.exists("/app/analyze.py"), (
        "The analyzer script /app/analyze.py does not exist. "
        "Create it to parse /var/log/remote.log and generate /app/report.html."
    )


def test_analyzer_generates_report():
    """Verify that running the analyzer generates report.html with facility/severity statistics."""
    # Send test messages with specific facilities and severities
    _run("logger -p user.info 'facility_test_message_user_info'")
    _run("logger -p daemon.warning 'facility_test_message_daemon_warning'")
    _run("logger -p kern.err 'facility_test_message_kern_err'")
    _run("logger -p local3.notice 'facility_test_message_local3_notice'")
    _run("logger -p auth.crit 'facility_test_message_auth_crit'")
    time.sleep(2)

    # Remove old report
    _run("rm -f /app/report.html")

    # Run the analyzer
    out, err, rc = _run("python3 /app/analyze.py", timeout=30)

    # Check report.html exists
    assert os.path.exists("/app/report.html"), (
        f"Running /app/analyze.py did not create /app/report.html.\n"
        f"Script stdout: {out}\nScript stderr: {err}"
    )

    # Read report content
    with open("/app/report.html", "r") as f:
        report = f.read()

    # Check report contains total count
    assert ("Total" in report or "total" in report.lower()), (
        "Report does not contain total message count."
    )

    # Check that facility names appear in the report (should have facility breakdown)
    facilities_expected = ["user", "daemon", "kern", "local3", "auth"]
    found_facilities = [f for f in facilities_expected if f.lower() in report.lower()]
    assert len(found_facilities) >= 3, (
        f"Report should include a facility breakdown. Expected at least 3 of {facilities_expected}.\n"
        f"Found: {found_facilities}\nReport content: {report[:1500]}"
    )

    # Check that severity names appear
    severities_expected = ["info", "warning", "err", "notice", "crit"]
    found_severities = [s for s in severities_expected if s.lower() in report.lower()]
    assert len(found_severities) >= 3, (
        f"Report should include a severity breakdown. Expected at least 3 of {severities_expected}.\n"
        f"Found: {found_severities}\nReport content: {report[:1500]}"
    )

    # Check that it's valid HTML
    assert ("<html" in report.lower() or "<!doctype" in report.lower()
            or "<table" in report.lower() or "<body" in report.lower()), (
        "Report does not appear to be HTML. Expected HTML structural tags."
    )

    # Check that a timestamp is present
    import re
    has_timestamp = bool(re.search(
        r'\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|\w{3}\s+\d{1,2}\s+\d{2}:\d{2}',
        report
    ))
    assert has_timestamp, (
        "Report should include a generated timestamp."
    )


def test_nginx_serves_report():
    """Verify nginx is running and serves report.html on port 8088."""
    # Check nginx is running
    out, err, rc = _run("pgrep nginx || systemctl is-active nginx 2>/dev/null || nginx -t 2>&1")
    # If nginx is not running, try to start it
    if rc != 0:
        _run("nginx 2>/dev/null || service nginx start 2>/dev/null")
        time.sleep(1)

    # Try to fetch the report via nginx
    out, err, rc = _run("curl -s -o /dev/null -w '%{http_code}' http://localhost:8088/report.html 2>&1", timeout=10)

    # The HTTP status code should be 200
    assert "200" in out.strip(), (
        f"Expected HTTP 200 from http://localhost:8088/report.html, got: {out.strip()}\n"
        f"Error output: {err}"
    )

    # Also check the actual content
    out, err, rc = _run("curl -s http://localhost:8088/report.html 2>&1", timeout=10)
    content = out + err

    assert "Total" in content or "total" in content or "<html" in content.lower(), (
        f"Response from http://localhost:8088/report.html does not look like the report.\n"
        f"Response: {content[:500]}"
    )
