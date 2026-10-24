#!/usr/bin/env python3
"""Carry the overfit-bench scheduler socket between a MAST host and this devvm.

The bench scheduler listens on a unix socket on the trainer's host; the
rewriter (codex through the local MetaGen proxy) has to run on the devvm. The
two share only the job directory on the mount, so the relay moves the socket's
JSON lines through it, one small file per batch:

    front <job>   devvm: binds the socket path scheduler.json names (so
                  RewriterClient connects unmodified) and opens a session
                  under <job>/relay/ per client connection.
    back <job>    trainer host: connects each session to the real socket.

<job>/relay/<session>/{up,down}/<seq>.jsonl carry the bytes, each ending in an
END line (a reader that sees a batch without it reads it again later; a rename
costs a third of a second on the mount); a ``closed`` file (holding the last
seq) ends one direction. Read batches are deleted off the hot path.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

POLL_SEC = 0.1
END = b"#END\n"
log = logging.getLogger("ofb_relay")


class Spool:
    """One direction of a session: ordered batch files plus a ``closed`` marker."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.seq = 0

    def write(self, lines: list[bytes]) -> None:
        (self.path / f"{self.seq:08d}.jsonl").write_bytes(b"".join(lines) + END)
        self.seq += 1

    def close(self) -> None:
        tmp = self.path / ".tmp-closed"
        tmp.write_text(str(self.seq))
        os.rename(tmp, self.path / "closed")

    def read_forever(self, sink, stop: threading.Event) -> None:
        """Hand each batch to ``sink`` in order until ``closed`` and all batches are read."""
        seq = 0
        while not stop.is_set():
            f = self.path / f"{seq:08d}.jsonl"
            try:
                data = f.read_bytes()
            except FileNotFoundError:
                data = None
            if data is not None and data.endswith(END):
                if len(data) > len(END):
                    sink(data[: -len(END)])
                threading.Thread(target=f.unlink, kwargs={"missing_ok": True}, daemon=True).start()
                seq += 1
                continue
            closed = self.path / "closed"
            if closed.exists():
                try:
                    last = int(closed.read_text() or 0)
                except ValueError:
                    last = 0
                if seq >= last:
                    return
            time.sleep(POLL_SEC)


def pump(sock: socket.socket, out: Spool, inp: Spool, name: str) -> None:
    """socket -> out spool, inp spool -> socket, until either side ends."""
    stop = threading.Event()

    def reader() -> None:
        buf = b""
        pending: list[bytes] = []
        last_flush = time.monotonic()
        sock.settimeout(POLL_SEC)
        try:
            while not stop.is_set():
                try:
                    chunk = sock.recv(1 << 16)
                except socket.timeout:
                    chunk = None
                if chunk == b"":
                    break
                if chunk:
                    buf += chunk
                    *full, buf = buf.split(b"\n")
                    pending.extend(line + b"\n" for line in full)
                if pending and (time.monotonic() - last_flush >= POLL_SEC or len(pending) >= 256):
                    out.write(pending)
                    pending, last_flush = [], time.monotonic()
        except OSError as e:
            log.info("%s: socket read ended: %s", name, e)
        finally:
            if pending:
                out.write(pending)
            out.close()
            stop.set()

    def send(data: bytes) -> None:
        sock.sendall(data)

    t = threading.Thread(target=reader, name=f"{name}-read", daemon=True)
    t.start()
    try:
        inp.read_forever(send, stop)
    except OSError as e:
        log.info("%s: socket write ended: %s", name, e)
    stop.set()
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()
    t.join(timeout=5)
    log.info("%s: session ended", name)


def read_scheduler(job: Path) -> dict | None:
    try:
        return json.loads((job / "scheduler.json").read_text())
    except (OSError, ValueError):
        return None


def front(job: Path) -> None:
    relay = job / "relay"
    me = socket.gethostname()
    served: tuple | None = None
    server: socket.socket | None = None
    while True:
        info = read_scheduler(job)
        key = None if info is None else (info.get("socket"), info.get("pid"), info.get("started"))
        if key != served:
            if server is not None:
                server.close()
                server = None
            served = key
            if info and info.get("host") != me and info.get("socket"):
                path = Path(info["socket"])
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.unlink(missing_ok=True)
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(path))
                server.listen(8)
                server.settimeout(1.0)
                log.info("front: serving %s for scheduler on %s pid %s", path, info.get("host"), info.get("pid"))
        if server is None:
            time.sleep(2)
            continue
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        except OSError:
            server = None
            served = None
            continue
        session = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        d = relay / session
        d.mkdir(parents=True)
        (d / "target.json").write_text(json.dumps({"socket": info["socket"], "pid": info.get("pid"),
                                                     "started": info.get("started")}))
        log.info("front: client connected, session %s", session)

        def run(conn=conn, d=d, session=session) -> None:
            pump(conn, Spool(d / "up"), Spool(d / "down"), f"front {session}")
            shutil.rmtree(d, ignore_errors=True)

        threading.Thread(target=run, daemon=True).start()


def back(job: Path) -> None:
    relay = job / "relay"
    me = socket.gethostname()
    handled: set[str] = set()
    while True:
        info = read_scheduler(job)
        if info and info.get("host") == me and relay.is_dir():
            for d in sorted(relay.iterdir()):
                if d.name in handled or not (d / "target.json").exists():
                    continue
                handled.add(d.name)
                try:
                    target = json.loads((d / "target.json").read_text())
                except (OSError, ValueError):
                    continue
                if (d / "up" / "closed").exists():
                    continue
                if (target.get("pid"), target.get("started")) != (info.get("pid"), info.get("started")):
                    log.info("back: session %s is for another scheduler; closing it", d.name)
                    Spool(d / "down").close()
                    continue
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    sock.connect(info["socket"])
                except OSError as e:
                    log.warning("back: session %s: cannot reach %s: %s", d.name, info["socket"], e)
                    sock.close()
                    Spool(d / "down").close()
                    continue
                log.info("back: session %s connected to %s", d.name, info["socket"])
                threading.Thread(
                    target=pump, args=(sock, Spool(d / "down"), Spool(d / "up"), f"back {d.name}"),
                    daemon=True,
                ).start()
        time.sleep(1.0)


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("front", "back"):
        raise SystemExit("usage: ofb_socket_relay.py {front|back} <job dir>")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    job = Path(sys.argv[2]).resolve()
    (front if sys.argv[1] == "front" else back)(job)


if __name__ == "__main__":
    main()
