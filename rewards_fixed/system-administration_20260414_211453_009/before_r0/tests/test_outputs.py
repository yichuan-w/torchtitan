import subprocess
import json
import time


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
        ["redis-cli", "ping"],
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
    """Test that the API can communicate with Redis."""
    result = subprocess.run(
        ["curl", "-s", "http://localhost:8080/api/jobs"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"API jobs endpoint request failed: {result.stderr}"
    
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        assert False, f"API jobs endpoint did not return valid JSON: {result.stdout}"
    
    assert "pending_jobs" in data, f"API jobs response missing pending_jobs: {data}"
