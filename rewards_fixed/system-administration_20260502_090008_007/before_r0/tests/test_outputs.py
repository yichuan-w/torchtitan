# Tests for supervisord multi-service dependency resolution task
# These tests verify that the solver has correctly fixed the supervisord configuration
# and all services start in the correct order

import subprocess
import time
import os


def run_supervisorctl(args, check=True, timeout=30):
    """Helper to run supervisorctl commands."""
    result = subprocess.run(
        ['supervisorctl'] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"supervisorctl {' '.join(args)} failed: {result.stderr}")
    return result


def run_command(cmd, timeout=30):
    """Helper to run shell commands."""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


def wait_for_process(process_name, timeout=60):
    """Wait for a process to be in RUNNING state."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = run_supervisorctl(['status', process_name], check=False)
        if 'RUNNING' in result.stdout:
            return True
        time.sleep(2)
    return False


def test_supervisord_running():
    """Test that supervisord is running and managing all processes."""
    result = run_command('pgrep -f supervisord')
    assert result.returncode == 0, "supervisord is not running"
    
    # Check that supervisorctl can connect (try with explicit config)
    status_result = run_command('supervisorctl -c /etc/supervisor/supervisord.conf status', timeout=15)
    # The return code should be 0 or 3 (processes may have different states)
    # Return code 4 means connection refused
    assert status_result.returncode != 4, \
        f"supervisorctl cannot connect to supervisord: {status_result.stdout}"


def test_appdb_running():
    """Test that the PostgreSQL database process is running."""
    assert wait_for_process('appdb', timeout=60), \
        "appdb failed to become RUNNING within timeout"
    
    result = run_supervisorctl(['status', 'appdb'], check=False)
    assert 'RUNNING' in result.stdout, \
        f"appdb is not RUNNING (status: {result.stdout})"


def test_appcache_running():
    """Test that the Redis cache process is running."""
    assert wait_for_process('appcache', timeout=60), \
        "appcache failed to become RUNNING within timeout"
    
    result = run_supervisorctl(['status', 'appcache'], check=False)
    assert 'RUNNING' in result.stdout, \
        f"appcache is not RUNNING (status: {result.stdout})"


def test_appserver_running():
    """Test that the application server process is running."""
    assert wait_for_process('appserver', timeout=60), \
        "appserver failed to become RUNNING within timeout"
    
    result = run_supervisorctl(['status', 'appserver'], check=False)
    assert 'RUNNING' in result.stdout, \
        f"appserver is not RUNNING (status: {result.stdout})"


def test_appworker_running():
    """Test that the background worker process is running."""
    assert wait_for_process('appworker', timeout=60), \
        "appworker failed to become RUNNING within timeout"
    
    result = run_supervisorctl(['status', 'appworker'], check=False)
    assert 'RUNNING' in result.stdout, \
        f"appworker is not RUNNING (status: {result.stdout})"


def test_appproxy_running():
    """Test that the Nginx proxy process is running."""
    assert wait_for_process('appproxy', timeout=60), \
        "appproxy failed to become RUNNING within timeout"
    
    result = run_supervisorctl(['status', 'appproxy'], check=False)
    assert 'RUNNING' in result.stdout, \
        f"appproxy is not RUNNING (status: {result.stdout})"


def test_database_initialized():
    """Test that the database has been initialized with required tables."""
    result = run_command(
        'PGPASSWORD=apppass123 psql -h localhost -U appuser -d appdb -c "\\dt items"',
        timeout=15
    )
    assert 'items' in result.stdout, \
        f"Database table 'items' not found. Database may not be initialized properly."


def test_application_accessible_on_port_80():
    """Test that the application is accessible via Nginx on port 80."""
    # Wait for services to stabilize
    time.sleep(3)
    
    # Try to connect to the application
    max_retries = 15
    last_result = None
    
    for i in range(max_retries):
        result = run_command('curl -s -o /dev/null -w "%{http_code}" http://localhost/health')
        if result.stdout.strip() == '200':
            return  # Success
        time.sleep(2)
    
    # Final check with detailed output
    result = run_command('curl -s http://localhost/health', timeout=15)
    assert result.returncode == 0, \
        f"Failed to connect to application: {result.stderr}"
    
    assert 'connected' in result.stdout.lower() or 'database' in result.stdout.lower(), \
        f"Application health check returned unexpected response: {result.stdout}"


def test_database_connection():
    """Test that the application can connect to the database."""
    result = run_command('curl -s http://localhost/health', timeout=15)
    assert result.returncode == 0, f"Health check failed: {result.stderr}"
    
    # The response should show database as connected
    import json
    try:
        health_data = json.loads(result.stdout)
        db_status = health_data.get('database', '')
        assert db_status == 'connected', \
            f"Database not connected (status: {db_status})"
    except json.JSONDecodeError:
        assert 'connected' in result.stdout.lower(), \
            f"Database not connected in response: {result.stdout}"


def test_cache_connection():
    """Test that the application can connect to Redis cache."""
    result = run_command('curl -s http://localhost/health', timeout=15)
    assert result.returncode == 0, f"Health check failed: {result.stderr}"
    
    # The response should show cache as connected
    import json
    try:
        health_data = json.loads(result.stdout)
        cache_status = health_data.get('cache', '')
        assert cache_status == 'connected', \
            f"Cache not connected (status: {cache_status})"
    except json.JSONDecodeError:
        assert 'connected' in result.stdout.lower(), \
            f"Cache not connected in response: {result.stdout}"
