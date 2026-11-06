"""
Tests for the inventory API async blocking issue.
Verifies that the server handles concurrent requests without blocking the event loop.
"""

import subprocess
import time
import json
import os
import signal


def run_load_test(num_requests=15):
    """Helper function to run the load test and return results."""
    # Start server in background
    proc = subprocess.Popen(
        ["./start_server.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
        cwd="/app"
    )
    
    try:
        time.sleep(5)
        
        # Run load test - use the venv Python explicitly
        result = subprocess.run(
            ["/opt/venv/bin/python", "load_test.py", "http://127.0.0.1:8000", str(num_requests)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd="/app"
        )
        
        return result, proc
    except Exception as e:
        return None, proc


def cleanup_server(proc):
    """Clean up server process."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except:
            pass


def test_server_handles_concurrent_requests():
    """
    Main test: Server should handle concurrent requests without timeouts.
    This test fails in the initial state due to event loop blocking.
    """
    result, proc = run_load_test(15)
    
    try:
        assert result is not None, "Load test failed to run"
        assert result.returncode == 0, (
            f"Load test failed with timeouts.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        
        # Verify no timeouts occurred
        assert "Timeouts: 0" in result.stdout, (
            f"Expected 0 timeouts but got:\n{result.stdout}"
        )
    finally:
        cleanup_server(proc)


def test_higher_load_concurrent_requests():
    """
    Test with higher load to ensure the fix is robust.
    This test also fails in the initial state.
    """
    result, proc = run_load_test(25)
    
    try:
        assert result is not None, "Load test failed to run"
        assert result.returncode == 0, (
            f"Load test with 25 requests failed.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        
        # Verify no timeouts occurred
        assert "Timeouts: 0" in result.stdout, (
            f"Expected 0 timeouts but got:\n{result.stdout}"
        )
    finally:
        cleanup_server(proc)
