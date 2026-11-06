import subprocess
import json
import os


def _run(cmd, timeout=30):
    """Run a shell command and return stdout, stderr, returncode."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def test_sysinfod_process_running():
    """Verify sysinfod process is running in the background."""
    out, err, rc = _run("pgrep -x sysinfod", timeout=10)
    assert rc == 0, f"sysinfod process not running (pgrep rc={rc}): {err}"
    pids = out.split()
    assert len(pids) >= 1, "No sysinfod process found"


def test_sysinfod_binary_installed():
    """Verify sysinfod binary is installed at /usr/local/bin/sysinfod."""
    assert os.path.exists("/usr/local/bin/sysinfod"), \
        "sysinfod binary not found at /usr/local/bin/sysinfod"
    assert os.access("/usr/local/bin/sysinfod", os.X_OK), \
        "sysinfod binary is not executable"


def test_sysinfod_http_response():
    """Verify the HTTP endpoint returns valid JSON with expected fields."""
    out, err, rc = _run("curl -s http://localhost:8080/", timeout=15)
    assert rc == 0, f"curl failed (rc={rc}): stderr={err}"
    assert out, "Empty response from sysinfod"

    # Parse as JSON
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise AssertionError(f"Response is not valid JSON: {e}\nResponse: {out}")

    # Verify required fields
    required_fields = [
        "hostname", "kernel", "arch", "uptime_sec",
        "load_1m", "load_5m", "load_15m",
        "total_ram_mb", "free_ram_mb", "procs",
        "server_version"
    ]
    for field in required_fields:
        assert field in data, f"Missing required field '{field}' in JSON response"

    # Verify server version
    assert data["server_version"] == "1.0.0", \
        f"Expected server_version '1.0.0' but got '{data['server_version']}'"

    # Verify types and sanity
    assert isinstance(data["uptime_sec"], (int, float)), "uptime_sec should be numeric"
    assert isinstance(data["procs"], (int, float)), "procs should be numeric"
    assert isinstance(data["total_ram_mb"], (int, float)), "total_ram_mb should be numeric"
    assert data["total_ram_mb"] > 0, "total_ram_mb should be positive"
    assert data["procs"] > 0, "procs should be positive"


def test_sysinfod_survives_disconnect():
    """Verify sysinfod parent is init (PID 1), not a shell.

    The daemon must be properly backgrounded so it survives
    terminal disconnection. Its parent should be PID 1 (init).
    """
    out, err, rc = _run("pgrep -x sysinfod", timeout=10)
    assert rc == 0, "sysinfod not running"
    pid = out.split()[0]

    # Get parent PID
    ppid_out, _, _ = _run(f"ps -o ppid= -p {pid}", timeout=10)
    ppid = ppid_out.strip()
    assert ppid, "Could not determine parent PID"

    # Check parent is not a shell
    parent_comm, _, _ = _run(f"ps -o comm= -p {ppid}", timeout=10)
    parent_comm = parent_comm.strip()
    assert parent_comm not in ("bash", "sh", "zsh"), \
        f"sysinfod parent is '{parent_comm}' (PID {ppid}) — it will die when the shell exits"
