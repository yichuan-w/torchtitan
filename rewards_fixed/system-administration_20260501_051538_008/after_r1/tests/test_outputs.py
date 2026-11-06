"""
Tests for SSH reverse tunnel configuration.
Verifies that tunnels are properly configured and services are accessible.
"""

import subprocess
import time
import json
import os


def test_dashboard_tunnel_process_running():
    """Test that an SSH tunnel process for the dashboard is running."""
    result = subprocess.run(
        ["pgrep", "-f", "ssh.*9090.*8080"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Dashboard tunnel process not found. pgrep output: {result.stderr}"


def test_api_tunnel_process_running():
    """Test that an SSH tunnel process for the API server is running."""
    result = subprocess.run(
        ["pgrep", "-f", "ssh.*9091.*3000"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"API tunnel process not found. pgrep output: {result.stderr}"


def test_metrics_tunnel_process_running():
    """Test that an SSH tunnel process for the metrics server is running."""
    result = subprocess.run(
        ["pgrep", "-f", "ssh.*9092.*5000"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Metrics tunnel process not found. pgrep output: {result.stderr}"


def test_dashboard_accessible_via_tunnel():
    """Test that the internal dashboard is accessible at http://127.0.0.1:9090."""
    time.sleep(1)
    
    result = subprocess.run(
        ["curl", "-s", "-f", "http://127.0.0.1:9090"],
        capture_output=True,
        text=True,
        timeout=10
    )
    assert result.returncode == 0, f"Failed to connect to dashboard via tunnel: {result.stderr}"
    
    data = json.loads(result.stdout)
    assert data.get("service") == "internal-dashboard", f"Unexpected service name: {data.get('service')}"
    assert data.get("status") == "operational", f"Unexpected status: {data.get('status')}"


def test_api_accessible_via_tunnel():
    """Test that the API server is accessible at http://127.0.0.1:9091/health."""
    time.sleep(1)
    
    result = subprocess.run(
        ["curl", "-s", "-f", "http://127.0.0.1:9091/health"],
        capture_output=True,
        text=True,
        timeout=10
    )
    assert result.returncode == 0, f"Failed to connect to API via tunnel: {result.stderr}"
    
    data = json.loads(result.stdout)
    assert data.get("status") == "healthy", f"Unexpected status: {data.get('status')}"
    assert data.get("service") == "api-server", f"Unexpected service name: {data.get('service')}"


def test_metrics_accessible_via_tunnel():
    """Test that the metrics server is accessible at http://127.0.0.1:9092/metrics."""
    time.sleep(1)
    
    result = subprocess.run(
        ["curl", "-s", "-f", "http://127.0.0.1:9092/metrics"],
        capture_output=True,
        text=True,
        timeout=10
    )
    assert result.returncode == 0, f"Failed to connect to metrics via tunnel: {result.stderr}"
    
    # Verify metrics response contains expected fields
    assert "cpu_usage" in result.stdout.lower() or "metrics" in result.stdout.lower(), \
        f"Unexpected metrics response: {result.stdout[:200]}"


def test_ssh_config_allows_tcp_forwarding():
    """Test that the effective sshd policy permits remote forwarding."""
    result = subprocess.run(
        [
            "sshd", "-T", "-C",
            "user=tunnel,host=localhost,addr=127.0.0.1",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    forwarding = next(
        (
            line.partition(" ")[2].strip()
            for line in result.stdout.splitlines()
            if line.startswith("allowtcpforwarding ")
        ),
        "",
    )
    assert result.returncode == 0 and forwarding in {"yes", "all", "remote"}, (
        f"Effective SSH policy does not permit remote forwarding: {forwarding or result.stderr}"
    )


def test_ssh_keys_configured():
    """Test that SSH keys are properly configured for the tunnel user."""
    result = subprocess.run(
        ["id", "tunnel"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, "tunnel user does not exist"
    
    authorized_keys = "/home/tunnel/.ssh/authorized_keys"
    assert os.path.exists(authorized_keys), f"authorized_keys not found at {authorized_keys}"
    
    with open(authorized_keys, "r") as f:
        keys = f.read().strip()
    
    assert len(keys) > 0, "No SSH keys in authorized_keys file"


def test_remote_forwards_restricted_to_required_loopback_listeners():
    """Test the effective sshd policy for remote forwarding listeners."""
    result = subprocess.run(
        [
            "sshd", "-T", "-C",
            "user=tunnel,host=localhost,addr=127.0.0.1",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"Unable to read effective sshd configuration: {result.stderr}"

    settings = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        settings[key] = value.strip()

    permitted_listeners = set(settings.get("permitlisten", "").split())
    required_listeners = {
        "127.0.0.1:9090",
        "127.0.0.1:9091",
        "127.0.0.1:9092",
    }
    assert permitted_listeners == required_listeners, (
        f"Remote forwarding listeners are not restricted to the required set: "
        f"{sorted(permitted_listeners)}"
    )
    assert settings.get("gatewayports") != "yes", (
        "GatewayPorts yes changes authorized loopback forwards into wildcard listeners"
    )


def test_all_original_services_running():
    """Test that all original services are still running on their original ports."""
    # Dashboard
    result = subprocess.run(
        ["curl", "-s", "http://127.0.0.1:8080"],
        capture_output=True,
        text=True,
        timeout=5
    )
    assert result.returncode == 0, "Dashboard should still be running on 8080"
    
    # API
    result = subprocess.run(
        ["curl", "-s", "http://127.0.0.1:3000/health"],
        capture_output=True,
        text=True,
        timeout=5
    )
    assert result.returncode == 0, "API server should still be running on 3000"
    
    # Metrics
    result = subprocess.run(
        ["curl", "-s", "http://127.0.0.1:5000/metrics"],
        capture_output=True,
        text=True,
        timeout=5
    )
    assert result.returncode == 0, "Metrics server should still be running on 5000"
