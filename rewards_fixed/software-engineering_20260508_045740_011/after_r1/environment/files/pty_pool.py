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
import termios
import time
import uuid
from typing import Dict, Optional


class PTYSession:
    """A single bash shell session running in a PTY."""
    
    def __init__(self):
        self._master_fd: Optional[int] = None
        self._pid: Optional[int] = None
        self._closed = False

        pid, master_fd = pty.fork()
        if pid == 0:
            os.environ['TERM'] = 'dumb'
            os.environ['PS1'] = ''
            os.environ['PS2'] = ''
            os.execv('/bin/bash', ['bash', '--noprofile', '--norc', '-i'])

        self._pid = pid
        self._master_fd = master_fd
        self._set_nonblocking(master_fd)

        attributes = termios.tcgetattr(master_fd)
        attributes[3] &= ~termios.ECHO
        termios.tcsetattr(master_fd, termios.TCSANOW, attributes)
        self._read_output(0.1)
    
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

    def _write(self, data: bytes):
        view = memoryview(data)
        while view:
            try:
                written = os.write(self._master_fd, view)
                view = view[written:]
            except BlockingIOError:
                select.select([], [self._master_fd], [], 0.1)

    def run(self, command: str, timeout: float = 5.0) -> str:
        """Execute a command in the persistent shell and return its output."""
        if self._closed or self._master_fd is None:
            raise RuntimeError('PTY session is closed')

        marker = f'__PTY_DONE_{uuid.uuid4().hex}__'
        self._write(f'{command}\nprintf "\\n{marker}\\n"\n'.encode())

        output = ''
        deadline = time.monotonic() + timeout
        while marker not in output:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'command timed out after {timeout} seconds')
            output += self._read_output(min(0.1, remaining))

        output = output.split(marker, 1)[0]
        if output.endswith('\r\n'):
            output = output[:-2]
        elif output.endswith('\n'):
            output = output[:-1]
        return output.replace('\r\n', '\n')

    def close(self):
        """Close this session."""
        if self._closed:
            return
        self._closed = True

        if self._pid is not None:
            try:
                os.kill(self._pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                try:
                    waited, _ = os.waitpid(self._pid, os.WNOHANG)
                except ChildProcessError:
                    waited = self._pid
                if waited:
                    break
                time.sleep(0.02)
            else:
                try:
                    os.kill(self._pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(self._pid, 0)
                except ChildProcessError:
                    pass

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
        self._pid = None
        self._master_fd = None
    
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
