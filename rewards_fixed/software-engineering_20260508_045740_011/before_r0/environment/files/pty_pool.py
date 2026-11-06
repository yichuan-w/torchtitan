"""
PTY Session Pool - manages multiple bash shell sessions via pseudo-terminals.

This module provides a way to create and manage multiple interactive bash sessions,
execute commands in them, and capture output.
"""

import os
import pty
import select
import signal
import fcntl
import struct
import termios
from typing import Dict, Optional


class PTYSession:
    """A single bash shell session running in a PTY."""
    
    def __init__(self):
        self._master_fd: Optional[int] = None
        self._pid: Optional[int] = None
        self._closed = False
    
    def _set_nonblocking(self, fd: int):
        """Set a file descriptor to non-blocking mode."""
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    
    def _read_output(self, timeout: float) -> str:
        """Read available output from the PTY with timeout."""
        output = []
        remaining = timeout
        
        while remaining > 0:
            ready, _, _ = select.select([self._master_fd], [], [], min(0.1, remaining))
            if ready:
                try:
                    data = os.read(self._master_fd, 4096)
                    if data:
                        output.append(data.decode('utf-8', errors='replace'))
                except OSError:
                    break
            remaining -= 0.1
        
        return ''.join(output)
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class PTYPool:
    """A pool of named PTY sessions."""
    
    def __init__(self):
        self._sessions: Dict[str, PTYSession] = {}
    
    def create(self, name: str) -> PTYSession:
        """Create a new named session."""
        if name in self._sessions:
            raise ValueError(f"Session '{name}' already exists")
        session = PTYSession()
        self._sessions[name] = session
        return session
    
    def get(self, name: str) -> PTYSession:
        """Get an existing session by name."""
        if name not in self._sessions:
            raise KeyError(f"Session '{name}' not found")
        return self._sessions[name]
    
    def close(self):
        """Close all sessions."""
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
