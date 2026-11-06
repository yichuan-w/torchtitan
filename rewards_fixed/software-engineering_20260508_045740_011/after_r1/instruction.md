I have a partially implemented PTY session pool in `/app/pty_pool.py` that manages multiple bash shell sessions. Each session should be able to execute commands and capture output. The problem is that when I close a session or the pool itself, some processes aren't being cleaned up properly.

I noticed that if I spawn a session and run a command that starts a background process (like `sleep 100 &`), that process keeps running even after I call `session.close()`. Even worse, when I use `nohup` or when a child process ignores signals, those processes become orphans and keep running after my program exits.

The base implementation I have works for basic command execution, but I need you to fix the process termination logic. The `PTYSession.close()` method needs to properly terminate the entire process group, not just the main shell process. Also make sure that the pool's context manager properly closes all sessions when exiting.

Requirements:
- A `PTYSession` class that wraps a single bash shell running in a PTY
- `PTYSession.run(command: str, timeout: float = 5.0) -> str` - execute a command and return output
- `PTYSession.close()` - terminate the session and ALL child processes (including grandchildren)
- `PTYSession` should be usable as a context manager
- A `PTYPool` class that manages multiple named sessions
- `PTYPool.create(name: str) -> PTYSession` - create a new named session
- `PTYPool.get(name: str) -> PTYSession` - get an existing session
- `PTYPool.close()` - close all sessions
- `PTYPool` should be usable as a context manager
- When any session is closed, NO child or grandchild processes should remain running (including processes that ignore SIGHUP)
- The implementation should work on Linux with Python's standard library only

Put the fixed implementation in `/app/pty_pool.py`. The tests will verify that no orphan processes remain after closing sessions.
