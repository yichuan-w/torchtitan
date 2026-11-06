import subprocess
import os
import time
import glob
import socket


def test_tls_server_listening():
    """Verify rsyslog server is listening on port 6514 with TLS."""
    # Check port is open
    result = subprocess.run(
        ["nc", "-z", "localhost", "6514"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Port 6514 is not open. Is the rsyslog TLS server running?"
    )

    # Verify TLS handshake succeeds using openssl s_client
    result = subprocess.run(
        ["openssl", "s_client", "-connect", "localhost:6514",
         "-servername", "localhost"],
        input="", capture_output=True, text=True, timeout=15,
    )
    output = result.stdout + result.stderr
    assert "CONNECTED" in output or "BEGIN CERTIFICATE" in output, (
        f"TLS handshake to port 6514 failed.\nOutput: {output[:500]}"
    )

    # Verify a server certificate is presented
    assert "Server certificate" in output or "-----BEGIN CERTIFICATE-----" in output, (
        f"No server certificate presented during TLS handshake.\nOutput: {output[:500]}"
    )


def test_log_forwarding_works():
    """Verify that a test log message is forwarded and stored on the server."""
    # Generate a unique test message
    import uuid
    test_id = str(uuid.uuid4())[:8]
    test_msg = f"TEST_MARKER_{test_id}"

    # Send the test message using logger
    result = subprocess.run(
        ["logger", "-p", "local0.info", test_msg],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"logger command failed: {result.stderr}"

    # Wait for rsyslog to process and forward the message
    # Give it some time for queue processing
    found = False
    for attempt in range(15):
        time.sleep(1)
        # Search recursively in /var/log/remote/
        try:
            grep_result = subprocess.run(
                ["grep", "-r", test_msg, "/var/log/remote/"],
                capture_output=True, text=True, timeout=10,
            )
            if grep_result.returncode == 0:
                found = True
                break
        except Exception:
            pass

    assert found, (
        f"Test message '{test_msg}' was not found in /var/log/remote/ "
        f"after 15 seconds. Is log forwarding working?\n"
        f"Remote dir contents: {_ls_remote()}"
    )


def test_imfile_log_forwarding():
    """Verify that lines written to /var/log/app/access.log are forwarded
    to the TLS receiver with the correct tag and facility."""
    import uuid

    # Ensure the app log directory exists
    os.makedirs("/var/log/app", exist_ok=True)

    test_id = str(uuid.uuid4())[:8]
    test_line = f"IMFILE_TEST_LINE_{test_id}"

    # Write a line to the monitored file
    with open("/var/log/app/access.log", "a") as f:
        f.write(test_line + "\n")

    # Wait for imfile to pick it up and forward
    found = False
    for attempt in range(15):
        time.sleep(1)
        try:
            grep_result = subprocess.run(
                ["grep", "-r", test_line, "/var/log/remote/"],
                capture_output=True, text=True, timeout=10,
            )
            if grep_result.returncode == 0:
                found = True
                # Verify the tag is present
                output = grep_result.stdout
                assert "app-access" in output, (
                    f"Forwarded log line found but missing 'app-access' tag. "
                    f"Got: {output[:200]}"
                )
                break
        except Exception:
            pass

    assert found, (
        f"Imfile test line '{test_line}' was not found in /var/log/remote/ "
        f"after 15 seconds. Is imfile monitoring configured for "
        f"/var/log/app/access.log?\n"
        f"Remote dir contents: {_ls_remote()}"
    )


def test_log_directory_structure():
    """Verify the log directory structure follows the required format."""
    hostname = socket.gethostname()

    # If no logs have been forwarded yet, send one to trigger directory creation
    base = f"/var/log/remote/{hostname}"
    if not os.path.isdir(base):
        # Send a message to trigger directory creation
        subprocess.run(
            ["logger", "-p", "local0.info", "STRUCTURE_TEST_TRIGGER"],
            capture_output=True, timeout=10,
        )
        # Wait for it to be processed
        for _ in range(10):
            time.sleep(1)
            if os.path.isdir(base):
                break

    assert os.path.isdir(base), (
        f"Expected directory {base} does not exist after triggering log message. "
        f"Remote dir contents: {_ls_remote()}"
    )

    # There should be subdirectories for year/month/day and a syslog.log file
    found_syslog = False
    for root, dirs, files in os.walk(base):
        if "syslog.log" in files:
            found_syslog = True
            # Verify the path structure: <hostname>/<year>/<month>/<day>/syslog.log
            rel = os.path.relpath(root, base)
            parts = rel.split(os.sep)
            if rel != ".":
                # Should have year/month/day components
                assert len(parts) == 3, (
                    f"Expected year/month/day structure under {base}, "
                    f"got: {rel}"
                )
                # Year should be 4 digits, month 2 digits, day 2 digits
                assert parts[0].isdigit() and len(parts[0]) == 4, f"Invalid year: {parts[0]}"
                assert parts[1].isdigit() and len(parts[1]) == 2, f"Invalid month: {parts[1]}"
                assert parts[2].isdigit() and len(parts[2]) == 2, f"Invalid day: {parts[2]}"
            break

    assert found_syslog, (
        f"No syslog.log file found under {base}. "
        f"Directory structure: {_ls_remote()}"
    )


def test_logrotate_configured():
    """Verify logrotate is configured for the remote logs."""
    # Check for logrotate config in standard locations
    config_paths = [
        "/etc/logrotate.d/rsyslog-remote",
        "/etc/logrotate.d/rsyslog",
        "/etc/logrotate.conf",
    ]

    # Also check for include of /etc/logrotate.d in main config
    found_config = False
    for path in config_paths:
        if os.path.exists(path):
            content = open(path).read()
            if "/var/log/remote" in content:
                found_config = True
                break

    # Also check /etc/logrotate.d/ directory for any file referencing remote
    if not found_config and os.path.isdir("/etc/logrotate.d"):
        for fname in os.listdir("/etc/logrotate.d"):
            fpath = os.path.join("/etc/logrotate.d", fname)
            if os.path.isfile(fpath):
                try:
                    content = open(fpath).read()
                    if "/var/log/remote" in content:
                        found_config = True
                        break
                except Exception:
                    pass

    assert found_config, (
        "No logrotate configuration found for /var/log/remote. "
        "Expected a logrotate config that rotates logs in /var/log/remote/."
    )


def _ls_remote():
    """Helper to list contents of /var/log/remote/ for error messages."""
    try:
        result = subprocess.run(
            ["find", "/var/log/remote", "-type", "f", "-o", "-type", "d"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout if result.stdout else "(empty)"
    except Exception:
        return "(could not list)"
