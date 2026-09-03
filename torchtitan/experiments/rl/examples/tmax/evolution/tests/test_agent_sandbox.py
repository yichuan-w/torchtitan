from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_sandbox as asb


class _FakeServer:
    """A unix-socket server speaking the sandbox protocol, without Daytona."""

    def __init__(self, handler):
        self.path = tempfile.mktemp(prefix="asb-test-", suffix=".sock", dir="/tmp")
        self.handler = handler
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(4)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                resp = self.handler(json.loads(data.decode() or "{}"))
                conn.sendall((json.dumps(resp) + "\n").encode())

    def close(self):
        self.sock.close()
        os.unlink(self.path)


def test_up_spawns_serve_with_pkg_before_the_subcommand(tmp_path, monkeypatch) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    seen = {}

    class FakeProc:
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            # The server's first act is to publish its state; do that here so
            # cmd_up's poll returns without a sandbox.
            asb._write_state(pkg, {"status": "ready", "sock": "/tmp/none.sock",
                                   "sandbox_id": "sb-1", "workdir": "/app"})

        def poll(self):
            return None

    monkeypatch.setattr(asb.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(asb, "_alive", lambda _state: False)

    assert asb.cmd_up(pkg) == 0
    argv = seen["argv"]
    assert argv[0] == sys.executable
    assert argv[argv.index("--pkg") + 1] == str(pkg)
    assert argv.index("--pkg") < argv.index("serve")
    assert argv[argv.index("serve") + 1] == "--sock"
    assert (pkg / "run" / "sandbox.log").exists()


def test_exec_passes_through_output_and_exit_code(tmp_path, capsys) -> None:
    pkg = tmp_path / "pkg"
    seen = {}

    def handler(req):
        seen.update(req)
        return {"ok": True, "code": 3, "stdout": "out\n", "stderr": "err\n"}

    server = _FakeServer(handler)
    try:
        asb._write_state(pkg, {"status": "ready", "sock": server.path})
        rc = asb.cmd_exec(pkg, "exit 3", 45)
    finally:
        server.close()

    captured = capsys.readouterr()
    assert rc == 3
    assert seen == {"op": "exec", "cmd": "exit 3", "timeout": 45}
    assert captured.out == "out\n"
    assert "err\n" in captured.err and "[exit 3]" in captured.err


def test_check_records_a_null_probe_pass_as_a_failure(tmp_path, monkeypatch, capsys) -> None:
    pkg = tmp_path / "pkg"
    calls = []

    def handler(req):
        calls.append(req["op"])
        if req["op"] == "grade":
            return {"ok": True, "reward": 1.0}
        return {"ok": True}

    server = _FakeServer(handler)
    try:
        asb._write_state(pkg, {"status": "down"})
        monkeypatch.setattr(asb, "cmd_up", lambda _pkg: (
            asb._write_state(pkg, {"status": "ready", "sock": server.path}) or 0))
        rc = asb.cmd_check(pkg, 30)
    finally:
        server.close()

    assert rc == 1
    assert calls == ["grade"]
    assert "VERDICT: fail   stage=null_probe" in capsys.readouterr().out
    record = json.loads((pkg / "run" / "checks.jsonl").read_text().strip())
    assert record["verdict"] == "fail" and record["stage"] == "null_probe"


def test_request_without_a_server_is_an_error_not_a_hang(tmp_path) -> None:
    r = asb._request({"sock": "/tmp/definitely-not-there.sock"}, "ping", wait=1)
    assert r["ok"] is False and "not reachable" in r["error"]
    assert asb._request({}, "ping")["ok"] is False
