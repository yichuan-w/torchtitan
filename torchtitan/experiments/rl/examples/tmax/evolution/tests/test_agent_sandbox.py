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
        monkeypatch.setattr(asb, "cmd_up", lambda _pkg, at_max=False: (
            asb._write_state(pkg, {"status": "ready", "sock": server.path}) or 0))
        rc = asb.cmd_check(pkg, 30)
    finally:
        server.close()

    assert rc == 1
    assert calls == ["grade"]
    assert "VERDICT: fail   stage=null_probe" in capsys.readouterr().out
    record = json.loads((pkg / "run" / "checks.jsonl").read_text().strip())
    assert record["verdict"] == "fail" and record["stage"] == "null_probe"


def test_check_returns_nonzero_when_the_oracle_fails(tmp_path, monkeypatch, capsys) -> None:
    pkg = tmp_path / "pkg"

    def handler(req):
        if req["op"] == "grade":            # null probe: untouched fails, correct
            return {"ok": True, "reward": 0.0}
        if req["op"] == "oracle":           # reference solution does not pass
            return {"ok": True, "reward": 0.0, "solve_exit": 7, "tail": "boom"}
        return {"ok": True}

    server = _FakeServer(handler)
    try:
        asb._write_state(pkg, {"status": "down"})
        monkeypatch.setattr(asb, "cmd_up", lambda _pkg, at_max=False: (
            asb._write_state(pkg, {"status": "ready", "sock": server.path}) or 0))
        rc = asb.cmd_check(pkg, 30)
    finally:
        server.close()

    assert rc == 1
    out = capsys.readouterr().out
    assert "VERDICT: fail" in out and "solve_exit=7" in out
    record = json.loads((pkg / "run" / "checks.jsonl").read_text().strip())
    assert record["verdict"] == "fail" and record["stage"] == "oracle"


def test_request_without_a_server_is_an_error_not_a_hang(tmp_path) -> None:
    r = asb._request({"sock": "/tmp/definitely-not-there.sock"}, "ping", wait=1)
    assert r["ok"] is False and "not reachable" in r["error"]
    assert asb._request({}, "ping")["ok"] is False


def _fake_up(pkg, server):
    """cmd_up as the tests see it: publish a ready state for the fake server,
    at the box the real cmd_up would have opened."""
    def up(_pkg, at_max=False):
        asb._write_state(pkg, {"status": "ready", "sock": server.path,
                               "resources": asb._box(pkg, at_max), "at_max": at_max})
        return 0
    return up


def test_up_opens_the_box_run_resources_json_names(tmp_path, monkeypatch) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "run").mkdir(parents=True)
    seen = {}

    class FakeProc:
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            # As the real server does: merge into the state cmd_up published.
            asb._write_state(pkg, {**asb._read_state(pkg), "status": "ready",
                                   "sock": "/tmp/none.sock", "sandbox_id": "sb-1",
                                   "workdir": "/app"})

        def poll(self):
            return None

    monkeypatch.setattr(asb.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(asb, "_alive", lambda _state: False)

    # No file: the harness default, and no size flags at all.
    assert asb.cmd_up(pkg) == 0
    assert not {"--cpu", "--mem-gb", "--disk-gb"} & set(seen["argv"])
    assert asb._read_state(pkg)["resources"]["source"].startswith("harness_default")

    (pkg / "run" / "resources.json").write_text(
        '{"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}\n')
    assert asb.cmd_up(pkg) == 0
    argv = seen["argv"]
    assert argv.index("serve") < argv.index("--cpu")
    assert argv[argv.index("--cpu") + 1] == "1"
    assert argv[argv.index("--mem-gb") + 1] == "2"
    assert argv[argv.index("--disk-gb") + 1] == "2"
    assert asb._read_state(pkg)["resources"] == {
        "cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}

    # --max ignores the file and opens the platform ceiling.
    assert asb.cmd_up(pkg, at_max=True) == 0
    argv = seen["argv"]
    assert argv[argv.index("--cpu") + 1] == str(asb.CEILING["cpu"])
    assert argv[argv.index("--mem-gb") + 1] == str(asb.CEILING["mem_gb"])
    assert argv[argv.index("--disk-gb") + 1] == str(asb.CEILING["disk_gb"])
    assert asb._read_state(pkg)["resources"]["source"] == "ceiling"


def test_check_reruns_once_at_the_ceiling_when_the_box_starved_the_oracle(
        tmp_path, monkeypatch, capsys) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "run").mkdir(parents=True)
    (pkg / "run" / "resources.json").write_text(
        '{"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}\n')
    oracles = []

    def handler(req):
        if req["op"] == "grade":
            return {"ok": True, "reward": 0.0}
        if req["op"] == "oracle":
            oracles.append(req)
            if len(oracles) == 1:       # training-size box: the kernel killed it
                return {"ok": True, "reward": 0.0, "solve_exit": 137, "tail": "Killed",
                        "measured": {"oom_kill": 1, "mem_peak_mb": 2048.0,
                                     "solve_secs": 12.0}}
            return {"ok": True, "reward": 1.0, "solve_exit": 0, "tail": "",
                    "measured": {"oom_kill": 0, "mem_peak_mb": 3100.0,
                                 "cpu_seconds": 40.0, "df_used_mb": 900.0,
                                 "solve_secs": 30.0}}
        return {"ok": True}

    server = _FakeServer(handler)
    try:
        asb._write_state(pkg, {"status": "down"})
        monkeypatch.setattr(asb, "cmd_up", _fake_up(pkg, server))
        monkeypatch.setattr(asb, "cmd_down", lambda _pkg: (
            asb._write_state(pkg, {"status": "down"}) or 0))
        rc = asb.cmd_check(pkg, 30)
    finally:
        server.close()

    assert rc == 0
    assert len(oracles) == 2
    out = capsys.readouterr().out
    assert "oracle ran out of memory in the training-size box" in out
    assert "rerunning the check once at the platform ceiling" in out
    assert "VERDICT: pass" in out and "Passed at the ceiling" in out
    records = [json.loads(l) for l in (pkg / "run" / "checks.jsonl").read_text().splitlines()]
    assert [r["verdict"] for r in records] == ["fail", "pass"]
    assert records[0]["starved"] == "memory" and records[0]["at_max"] is False
    assert records[0]["resources"]["mem_gb"] == 2
    assert records[1]["at_max"] is True
    assert records[1]["resources"]["source"] == "ceiling"
    assert records[1]["measured"]["mem_peak_mb"] == 3100.0


def test_check_does_not_rerun_when_still_starved_at_the_ceiling(
        tmp_path, monkeypatch, capsys) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "run").mkdir(parents=True)
    oracles = []

    def handler(req):
        if req["op"] == "grade":
            return {"ok": True, "reward": 0.0}
        if req["op"] == "oracle":
            oracles.append(req)
            return {"ok": True, "reward": 0.0, "solve_exit": 137, "tail": "Killed",
                    "measured": {"oom_kill": 1, "mem_peak_mb": 8000.0, "solve_secs": 5.0}}
        return {"ok": True}

    server = _FakeServer(handler)
    try:
        asb._write_state(pkg, {"status": "down"})
        monkeypatch.setattr(asb, "cmd_up", _fake_up(pkg, server))
        monkeypatch.setattr(asb, "cmd_down", lambda _pkg: (
            asb._write_state(pkg, {"status": "down"}) or 0))
        rc = asb.cmd_check(pkg, 30)
    finally:
        server.close()

    assert rc == 1
    assert len(oracles) == 2        # once at training size, once at the ceiling, no more
    out = capsys.readouterr().out
    assert "Still out of memory at the platform ceiling" in out
