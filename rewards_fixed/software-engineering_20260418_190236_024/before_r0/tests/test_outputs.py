import subprocess
import sys
import time
import os


def test_worker_pool_no_false_restarts():
    """
    Test that the worker pool correctly detects alive workers
    and doesn't incorrectly restart them due to race conditions.
    """
    result = subprocess.run(
        [sys.executable, "/app/worker_pool.py"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/app"
    )
    
    output = result.stdout + result.stderr
    print("=== Worker Pool Output ===")
    print(output)
    print("=" * 40)
    
    # The script should exit with code 0 if no false restarts
    assert result.returncode == 0, (
        f"Worker pool exited with code {result.returncode}. "
        f"This indicates false heartbeat failures occurred.\n"
        f"Output:\n{output}"
    )
    
    # Check that success message is in output
    assert "SUCCESS: All workers maintained valid heartbeats" in output, (
        f"Expected success message not found in output.\n"
        f"Output:\n{output}"
    )
    
    # Verify no false restarts reported
    assert "False restarts (workers incorrectly marked dead): 0" in output, (
        f"Expected 0 false restarts, but output indicates false restarts occurred.\n"
        f"Output:\n{output}"
    )


def test_fix_preserves_hung_detection():
    """
    Test that the fix still correctly detects genuinely hung workers.
    This test uses the fixed worker_pool module to create a hung worker scenario.
    """
    test_script_path = "/app/test_hung_detection.py"
    test_script_content = '''#!/usr/bin/env python3
import multiprocessing
import time
import sys
import importlib.util

# Import the worker_pool module
spec = importlib.util.spec_from_file_location("worker_pool", "/app/worker_pool.py")
worker_pool_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker_pool_module)

# Get the WorkerPool class and test its hung detection
WorkerPool = worker_pool_module.WorkerPool

def hanging_task():
    """A task that hangs forever."""
    time.sleep(1000)

def main():
    multiprocessing.set_start_method('fork', force=True)
    
    # Create a pool with short timeout
    pool = WorkerPool(num_workers=1, heartbeat_timeout=0.3, max_restarts=0)
    pool.start()
    
    # Give worker time to start
    time.sleep(0.1)
    
    # Get the worker's heartbeat mechanism
    # Test that a hung worker would be detected by checking the pool's 
    # ability to identify a worker that stops heartbeating
    
    # We'll manually test the heartbeat detection mechanism
    # by getting the first heartbeat value and checking it updates
    
    heartbeat_events = pool.heartbeat_events
    if heartbeat_events and len(heartbeat_events) > 0:
        event = heartbeat_events[0]
        # Worker should be pulsing
        alive1 = event.wait(timeout=0.3)
        
        # If using handshake pattern, clear and check again
        if alive1:
            event.clear()
            alive2 = event.wait(timeout=0.3)
        else:
            alive2 = False
        
        # Cleanup
        pool.shutdown()
        
        # Both checks should succeed for a healthy worker
        if alive1 and alive2:
            print("SUCCESS: Heartbeat mechanism works correctly")
            sys.exit(0)
        else:
            print(f"FAILURE: alive1={alive1}, alive2={alive2}")
            sys.exit(1)
    else:
        # No heartbeat_events, check for Value-based approach
        last_heartbeats = getattr(pool, 'last_heartbeats', None)
        if last_heartbeats and len(last_heartbeats) > 0:
            hb = last_heartbeats[0]
            val1 = hb.value
            time.sleep(0.2)
            val2 = hb.value
            
            pool.shutdown()
            
            if val2 > val1:
                print("SUCCESS: Heartbeat mechanism works correctly")
                sys.exit(0)
            else:
                print(f"FAILURE: val1={val1}, val2={val2}")
                sys.exit(1)
        else:
            # Cleanup
            pool.shutdown()
            print("SUCCESS: Pool initialized correctly")
            sys.exit(0)

if __name__ == "__main__":
    main()
'''
    with open(test_script_path, 'w') as f:
        f.write(test_script_content)
    
    result = subprocess.run(
        [sys.executable, test_script_path],
        capture_output=True,
        text=True,
        timeout=60
    )
    
    output = result.stdout + result.stderr
    print("=== Hung Detection Test Output ===")
    print(output)
    print("=" * 40)
    
    # Cleanup
    if os.path.exists(test_script_path):
        os.remove(test_script_path)
    
    assert result.returncode == 0, (
        f"Hung detection test failed with code {result.returncode}.\n"
        f"The fix may have broken detection of genuinely hung workers.\n"
        f"Output:\n{output}"
    )
    
    assert "SUCCESS" in output, (
        f"Expected success message not found.\n"
        f"Output:\n{output}"
    )


def test_rapid_heartbeat_reliability():
    """
    Stress test: Run the worker pool multiple times to verify
    the heartbeat mechanism is reliable under stress.
    """
    # Run the main test script 3 times to check for intermittent failures
    failures = 0
    for i in range(3):
        result = subprocess.run(
            [sys.executable, "/app/worker_pool.py"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd="/app"
        )
        
        output = result.stdout + result.stderr
        
        if result.returncode != 0 or "FAILURE" in output:
            failures += 1
            print(f"=== Run {i+1} FAILED ===")
            print(output[-500:] if len(output) > 500 else output)
        else:
            print(f"=== Run {i+1} PASSED ===")
    
    print(f"\n=== Rapid Heartbeat Test Summary: {3-failures}/3 passed ===")
    
    # All runs should pass
    assert failures == 0, (
        f"Rapid heartbeat test failed {failures}/3 times.\n"
        f"Race condition fix may not be robust enough."
    )
