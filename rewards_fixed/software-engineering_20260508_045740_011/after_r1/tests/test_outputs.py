"""
Tests for PTY Session Pool implementation.
Verifies that sessions can execute commands and that all child processes
are properly terminated when sessions are closed.
"""

import os
import subprocess
import time
import sys

sys.path.insert(0, '/app')


def get_child_pids(ppid: int) -> list:
    """Get all descendant PIDs of a process (children, grandchildren, etc.)."""
    result = subprocess.run(
        ['pgrep', '-P', str(ppid)],
        capture_output=True, text=True
    )
    children = []
    if result.returncode == 0 and result.stdout.strip():
        for pid_str in result.stdout.strip().split('\n'):
            pid = int(pid_str)
            children.append(pid)
            # Recursively get grandchildren
            children.extend(get_child_pids(pid))
    return children


def process_exists(pid: int) -> bool:
    """Check if a process with given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_session_creation():
    """Test that PTYSession can be created and starts a shell."""
    from pty_pool import PTYSession
    session = PTYSession()
    # The session should have started a shell process
    assert hasattr(session, '_pid') and session._pid is not None, "Session should have a shell PID"
    assert hasattr(session, '_master_fd') and session._master_fd is not None, "Session should have a master FD"
    session.close()


def test_pool_creation():
    """Test that PTYPool can be created and close() works properly."""
    from pty_pool import PTYPool, PTYSession
    pool = PTYPool()
    # Pool should have a sessions dict
    assert hasattr(pool, '_sessions'), "Pool should have _sessions attribute"
    assert len(pool._sessions) == 0, "Pool should start empty"
    
    # Create a session to test proper close functionality
    session = pool.create('test')
    assert isinstance(session, PTYSession), "create should return PTYSession instance"
    
    # This should close all sessions without error
    pool.close()


def test_pool_named_sessions():
    """Test that pool can create and retrieve named sessions."""
    from pty_pool import PTYPool
    pool = PTYPool()
    session = pool.create('test_session')
    assert session is not None
    assert pool.get('test_session') is session
    pool.close()


def test_session_run_command():
    """Test that a session can run a command and return output."""
    from pty_pool import PTYSession
    session = PTYSession()
    try:
        output = session.run('echo "hello world"', timeout=5.0)
        assert 'hello world' in output
    finally:
        session.close()


def test_session_context_manager():
    """Test that PTYSession works as a context manager."""
    from pty_pool import PTYSession
    with PTYSession() as session:
        shell_pid = session._pid
        output = session.run('echo "test"', timeout=5.0)
        assert 'test' in output
    assert not process_exists(shell_pid), "Session context manager should close its shell"


def test_pool_context_manager():
    """Test that PTYPool works as a context manager."""
    from pty_pool import PTYPool
    with PTYPool() as pool:
        session = pool.create('session1')
        shell_pid = session._pid
        output = session.run('echo "pooled"', timeout=5.0)
        assert 'pooled' in output
    assert not process_exists(shell_pid), "Pool context manager should close its sessions"


def test_background_process_terminated_on_close():
    """Test that background processes are terminated when session closes."""
    from pty_pool import PTYSession
    session = PTYSession()
    
    # Run a background process and get its PID
    output = session.run('sleep 100 & echo $!', timeout=5.0)
    
    # Parse the PID from output
    lines = [l for l in output.strip().split('\n') if l.strip().isdigit()]
    assert len(lines) >= 1, f"Expected PID in output, got: {output}"
    bg_pid = int(lines[-1].strip())
    
    # Verify the process exists
    assert process_exists(bg_pid), f"Background process {bg_pid} should exist"
    
    # Close the session
    session.close()
    
    # Wait a moment for cleanup
    time.sleep(0.5)
    
    # Verify the background process is gone
    assert not process_exists(bg_pid), f"Background process {bg_pid} should be terminated after close()"


def test_nohup_process_terminated_on_close():
    """Test that nohup processes (which ignore SIGHUP) are still terminated."""
    from pty_pool import PTYSession
    session = PTYSession()
    
    # Run a nohup background process
    output = session.run('nohup sleep 100 > /dev/null 2>&1 & echo $!', timeout=5.0)
    
    # Parse the PID from output
    lines = [l for l in output.strip().split('\n') if l.strip().isdigit()]
    assert len(lines) >= 1, f"Expected PID in output, got: {output}"
    bg_pid = int(lines[-1].strip())
    
    # Verify the process exists
    assert process_exists(bg_pid), f"Nohup process {bg_pid} should exist"
    
    # Close the session
    session.close()
    
    # Wait a moment for cleanup
    time.sleep(0.5)
    
    # Verify the nohup process is gone (this tests proper process group termination)
    assert not process_exists(bg_pid), f"Nohup process {bg_pid} should be terminated after close()"


def test_nested_child_processes_terminated():
    """Test that nested/grandchild processes are terminated."""
    from pty_pool import PTYSession
    session = PTYSession()
    
    # Run a script that creates a child process which creates its own child
    script = '''
import subprocess
import time
p = subprocess.Popen(['sleep', '100'])
print(p.pid)
time.sleep(0.5)
'''
    output = session.run(f'python3 -c "{script}"', timeout=5.0)
    
    # Parse the PID from output
    lines = [l for l in output.strip().split('\n') if l.strip().isdigit()]
    assert len(lines) >= 1, f"Expected PID in output, got: {output}"
    child_pid = int(lines[-1].strip())
    
    # Verify the process exists
    assert process_exists(child_pid), f"Child process {child_pid} should exist"
    
    # Close the session
    session.close()
    
    # Wait a moment for cleanup
    time.sleep(0.5)
    
    # Verify the child process is gone
    assert not process_exists(child_pid), f"Child process {child_pid} should be terminated after close()"


def test_multiple_sessions_in_pool():
    """Test that pool can manage multiple sessions."""
    from pty_pool import PTYPool
    pool = PTYPool()
    
    session1 = pool.create('s1')
    session2 = pool.create('s2')
    
    output1 = session1.run('echo "session1"', timeout=5.0)
    output2 = session2.run('echo "session2"', timeout=5.0)
    
    assert 'session1' in output1
    assert 'session2' in output2
    
    pool.close()


def test_pool_close_terminates_all_sessions():
    """Test that closing the pool terminates all session processes."""
    from pty_pool import PTYPool
    pool = PTYPool()
    
    # Create sessions with background processes
    session1 = pool.create('s1')
    session2 = pool.create('s2')
    
    output1 = session1.run('sleep 100 & echo $!', timeout=5.0)
    output2 = session2.run('sleep 100 & echo $!', timeout=5.0)
    
    # Parse PIDs
    pid1 = int([l for l in output1.strip().split('\n') if l.strip().isdigit()][-1])
    pid2 = int([l for l in output2.strip().split('\n') if l.strip().isdigit()][-1])
    
    assert process_exists(pid1)
    assert process_exists(pid2)
    
    # Close the pool
    pool.close()
    
    # Wait for cleanup
    time.sleep(0.5)
    
    # Both should be gone
    assert not process_exists(pid1), f"Process {pid1} should be terminated"
    assert not process_exists(pid2), f"Process {pid2} should be terminated"


def test_session_shell_persists_between_commands():
    """Test that shell state persists between commands in a session."""
    from pty_pool import PTYSession
    session = PTYSession()
    
    # Set a variable
    session.run('export MY_VAR="test_value"', timeout=5.0)
    
    # Verify it persists
    output = session.run('echo $MY_VAR', timeout=5.0)
    assert 'test_value' in output
    
    session.close()


def test_no_zombie_processes():
    """Test that no zombie processes remain after closing sessions."""
    from pty_pool import PTYPool
    
    # Get initial count of zombie processes
    result = subprocess.run(
        ['ps', 'aux'],
        capture_output=True, text=True
    )
    initial_zombies = [l for l in result.stdout.split('\n') if '<defunct>' in l]
    
    # Create and close multiple sessions
    with PTYPool() as pool:
        for i in range(3):
            session = pool.create(f'session_{i}')
            session.run(f'echo test{i}', timeout=5.0)
            session.run('sleep 0.1 &', timeout=5.0)
    
    # Wait for cleanup
    time.sleep(1.0)
    
    # Check for zombie processes again
    result = subprocess.run(
        ['ps', 'aux'],
        capture_output=True, text=True
    )
    final_zombies = [l for l in result.stdout.split('\n') if '<defunct>' in l]
    
    # Should not have created new zombies
    assert len(final_zombies) <= len(initial_zombies), \
        f"Zombie processes detected: {final_zombies}"
