import subprocess
import json
import time
from decimal import Decimal


SUPERVISOR_CONFIG = "/opt/myapp/config/supervisord.conf"


def api_redis_url():
    result = subprocess.run(
        ["supervisorctl", "-c", SUPERVISOR_CONFIG, "status", "api"],
        capture_output=True,
        text=True,
        check=True,
    )
    pid = int(result.stdout.split("pid ", 1)[1].split(",", 1)[0])
    with open(f"/proc/{pid}/environ", "rb") as environment_file:
        environment = environment_file.read().split(b"\0")
    redis_url = next(
        (item.split(b"=", 1)[1].decode() for item in environment if item.startswith(b"REDIS_URL=")),
        "redis://localhost:6379",
    )
    return redis_url


def test_supervisord_running():
    """Test that supervisord is running."""
    result = subprocess.run(
        ["pgrep", "-f", "supervisord"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "supervisord is not running"


def test_all_services_running():
    """Test that all four services are in RUNNING state."""
    result = subprocess.run(
        ["supervisorctl", "-c", "/opt/myapp/config/supervisord.conf", "status"],
        capture_output=True,
        text=True,
    )
    
    output = result.stdout.lower()
    
    # Check each service is running
    services = ["redis", "worker", "api", "nginx"]
    for service in services:
        assert service in output, f"Service {service} not found in supervisorctl output"
        # Check for RUNNING state
        assert "running" in output, f"Service {service} is not running. Output: {result.stdout}"


def test_redis_accessible():
    """Test that Redis is accessible and responding."""
    result = subprocess.run(
        ["redis-cli", "--no-auth-warning", "-u", api_redis_url(), "ping"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Redis is not accessible: {result.stderr}"
    assert "PONG" in result.stdout, f"Redis did not respond with PONG: {result.stdout}"


def test_health_endpoint():
    """Test that the health endpoint returns healthy status."""
    result = subprocess.run(
        ["curl", "-s", "http://localhost:8080/health"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Health endpoint request failed: {result.stderr}"
    
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        assert False, f"Health endpoint did not return valid JSON: {result.stdout}"
    
    assert data.get("status") == "healthy", f"Health status not healthy: {data}"


def test_api_jobs_endpoint():
    """Test that the jobs endpoint reports its current Redis count."""
    redis_url = api_redis_url()
    subprocess.run(
        ["supervisorctl", "-c", SUPERVISOR_CONFIG, "stop", "worker"],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        count_result = subprocess.run(
            ["redis-cli", "--no-auth-warning", "-u", redis_url, "llen", "job_queue"],
            capture_output=True,
            text=True,
            check=True,
        )
        expected_count = Decimal(count_result.stdout.strip())
        monitor = subprocess.Popen(
            ["redis-cli", "--no-auth-warning", "-u", redis_url, "monitor"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert monitor.stdout.readline().strip() == "OK", "Redis MONITOR did not start"
        result = subprocess.run(
            ["curl", "-s", "http://localhost:8080/api/jobs"],
            capture_output=True,
            text=True,
        )
        time.sleep(0.2)
        monitor.terminate()
        monitor_output, _ = monitor.communicate(timeout=2)
        assert result.returncode == 0, f"API jobs endpoint request failed: {result.stderr}"

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            assert False, f"API jobs endpoint did not return valid JSON: {result.stdout}"

        assert Decimal(str(data.get("pending_jobs"))) == expected_count, f"API jobs response did not report Redis count {expected_count}: {data}"
        assert monitor_output.strip(), "API jobs request did not access Redis"
    finally:
        subprocess.run(
            ["supervisorctl", "-c", SUPERVISOR_CONFIG, "start", "worker"],
            capture_output=True,
            text=True,
        )
