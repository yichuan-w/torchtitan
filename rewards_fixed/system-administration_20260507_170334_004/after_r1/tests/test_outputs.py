"""
Tests for supervisord + nginx multi-service setup task.
Verifies that the solver has fixed all configuration issues.
"""

import subprocess
import time
import urllib.request
import json
import os


def run_cmd(cmd, timeout=10):
    """Run a shell command and return output."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout, result.stderr


def test_supervisord_running():
    """Verify supervisord is running AND can communicate with processes."""
    rc, out, err = run_cmd("supervisorctl status")
    # supervisord may be running but not properly configured
    # If supervisorctl can't connect, the test should fail
    assert rc == 0, f"supervisorctl failed - supervisord may not be running correctly: {err}"
    # Check that supervisorctl actually returns process info
    assert len(out.strip()) > 0, "supervisorctl returned empty output - no processes managed"


def test_all_backends_running():
    """Verify all three backend services are running in supervisor."""
    rc, out, _ = run_cmd("supervisorctl status")
    assert rc == 0, "Failed to get supervisorctl status"
    
    # Check each backend is RUNNING
    for backend in ['backend1', 'backend2', 'backend3']:
        assert backend in out, f"{backend} not found in supervisor status"
        # Check it's not FATAL or BACKOFF
        assert 'FATAL' not in out, f"Backend in FATAL state: {out}"
        assert 'BACKOFF' not in out, f"Backend in BACKOFF state: {out}"
        assert 'RUNNING' in out, f"Backend not in RUNNING state: {out}"


def test_backend1_health():
    """Verify backend1 responds to health checks."""
    rc, out, _ = run_cmd("curl -s http://localhost:8001/health", timeout=5)
    assert rc == 0, "Failed to reach backend1"
    
    try:
        data = json.loads(out)
        assert data.get('status') == 'healthy', f"backend1 not healthy: {data}"
        assert data.get('port') == 8001, f"Wrong port for backend1: {data}"
    except json.JSONDecodeError:
        assert False, f"backend1 returned invalid JSON: {out}"


def test_backend2_health():
    """Verify backend2 responds to health checks."""
    rc, out, _ = run_cmd("curl -s http://localhost:8002/health", timeout=5)
    assert rc == 0, "Failed to reach backend2"
    
    try:
        data = json.loads(out)
        assert data.get('status') == 'healthy', f"backend2 not healthy: {data}"
        assert data.get('port') == 8002, f"Wrong port for backend2: {data}"
    except json.JSONDecodeError:
        assert False, f"backend2 returned invalid JSON: {out}"


def test_backend3_health():
    """Verify backend3 responds to health checks."""
    rc, out, _ = run_cmd("curl -s http://localhost:8003/health", timeout=5)
    assert rc == 0, "Failed to reach backend3"
    
    try:
        data = json.loads(out)
        assert data.get('status') == 'healthy', f"backend3 not healthy: {data}"
        assert data.get('port') == 8003, f"Wrong port for backend3: {data}"
    except json.JSONDecodeError:
        assert False, f"backend3 returned invalid JSON: {out}"


def test_nginx_running():
    """Verify nginx is running AND configured correctly."""
    # Check nginx is running
    rc, out, _ = run_cmd("pgrep -f nginx")
    assert rc == 0, "nginx process not found"
    
    # Check nginx is listening on port 80 and responding
    rc, out, _ = run_cmd("curl -s http://localhost/nginx-health", timeout=5)
    assert rc == 0, "nginx not responding on port 80"
    assert "nginx is healthy" in out or "healthy" in out.lower(), f"nginx health check failed: {out}"


def test_nginx_proxy():
    """Verify nginx successfully proxies requests to backends."""
    # Make multiple requests and verify they succeed
    responses = []
    for _ in range(6):
        rc, out, _ = run_cmd("curl -s http://localhost/health", timeout=5)
        assert rc == 0, "Failed to get response from nginx proxy"
        responses.append(out)
        time.sleep(0.1)
    
    # All responses should be valid JSON with healthy status
    for resp in responses:
        try:
            data = json.loads(resp)
            assert data.get('status') == 'healthy', "Backend returned unhealthy status"
        except json.JSONDecodeError:
            assert False, f"Invalid JSON response from nginx proxy: {resp}"


def test_nginx_load_balancing():
    """Verify nginx load balances across all backends."""
    # Make multiple requests and check that different backends respond
    ports_seen = set()
    for _ in range(10):
        rc, out, _ = run_cmd("curl -s http://localhost/health", timeout=5)
        if rc == 0:
            try:
                data = json.loads(out)
                ports_seen.add(data.get('port'))
            except json.JSONDecodeError:
                pass
        time.sleep(0.1)
    
    # Should have seen at least 2 different backends (load balancing working)
    assert len(ports_seen) >= 2, f"Load balancing not working, only saw backends on ports: {ports_seen}"


def test_log_files_exist():
    """Verify log files are being created correctly."""
    # Check that log files exist for backends
    log_dir = "/var/log/app"
    
    # backend1 should have logs in /var/log/app
    assert os.path.exists(f"{log_dir}/backend1.log"), "backend1.log not found"
    
    # backend2 should have logs in the correct directory after fix
    # The fix should have created /var/log/app or changed the config
    backend2_has_logs = (
        os.path.exists(f"{log_dir}/backend2.log") or
        os.path.exists("/var/log/app2/backend2.log")
    )
    assert backend2_has_logs, "backend2.log not found in expected locations"
    
    # backend3 should have logs
    assert os.path.exists(f"{log_dir}/backend3.log"), "backend3.log not found"


def test_log_rotation_configured():
    """Verify log rotation is properly configured in supervisor."""
    # Check backend.conf for proper log rotation settings
    conf_file = "/etc/supervisor/conf.d/backend.conf"
    assert os.path.exists(conf_file), "backend.conf not found"
    
    with open(conf_file, 'r') as f:
        content = f.read()
    
    # Check that stdout_logfile_maxbytes is set to a reasonable value
    # Should have 'MB', 'KB', 'GB', or 'bytes' suffix
    # The original bug had just '10' without a unit
    import re
    
    # Find all stdout_logfile_maxbytes values
    maxbytes_matches = re.findall(r'stdout_logfile_maxbytes\s*=\s*(\S+)', content)
    
    for match in maxbytes_matches:
        # Should end with a size unit or be a number followed by bytes
        valid_units = ['KB', 'MB', 'GB', 'TB']
        is_valid = any(match.endswith(unit) for unit in valid_units) or match == '0'
        assert is_valid, f"Invalid log rotation size '{match}' - should have unit suffix (KB, MB, GB) or be 0"


def test_event_listener_running():
    """Verify the event listener is running and capturing events."""
    # Check event listener status
    rc, out, _ = run_cmd("supervisorctl status process_state")
    assert rc == 0, "Failed to check event listener status"
    
    # Event listener should be RUNNING
    assert "RUNNING" in out, f"Event listener not running: {out}"
    assert "FATAL" not in out, f"Event listener in FATAL state: {out}"
    assert "BACKOFF" not in out, f"Event listener in BACKOFF state: {out}"


def test_event_listener_configured():
    """Verify event listener is properly configured."""
    conf_file = "/etc/supervisor/conf.d/backend.conf"
    assert os.path.exists(conf_file), "backend.conf not found"
    
    with open(conf_file, 'r') as f:
        content = f.read()
    
    # Check that event listener has autostart=true (not false)
    assert "[eventlistener:process_state]" in content, "Event listener section not found"
    
    # Check autostart is enabled
    import re
    autostart_match = re.search(r'\[eventlistener:process_state\].*?autostart\s*=\s*(\S+)', content, re.DOTALL)
    if autostart_match:
        autostart_value = autostart_match.group(1)
        assert autostart_value.lower() == 'true', f"Event listener autostart should be true, got: {autostart_value}"



