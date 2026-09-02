#!/usr/bin/env python3
"""Allowlisted HTTP egress proxy for della compute nodes.

Runs on della-tridao (which has internet). SLURM compute nodes (which do
not) point HTTPS_PROXY / HTTP_PROXY at it and can reach ONLY the
allowlisted destination domains:

    export HTTPS_PROXY=http://172.17.2.45:3129
    export HTTP_PROXY=http://172.17.2.45:3129
    export NO_PROXY=localhost,127.0.0.1,.princeton.edu,172.16.0.0/12

Supports:
  * CONNECT (HTTPS tunnelling)  -- the main path
  * absolute-form plain HTTP requests (GET http://host/... HTTP/1.1)

stdlib only (asyncio), Python >= 3.8. No sudo, binds one high port.

Config (env vars, all optional):
  EGRESS_PROXY_PORT    listen port            (default 3129)
  EGRESS_PROXY_BIND    bind address           (default 0.0.0.0)
  EGRESS_PROXY_ALLOW   extra comma-separated domain suffixes to allow
  EGRESS_PROXY_LOG     log file path
      (default /scratch/gpfs/TRIDAO/al9080/terminal-rl/logs/egress_proxy.log)

Log format: one line per request:
  <iso-timestamp> src=<ip>:<port> method=<CONNECT|GET|...> dst=<host>:<port> <ALLOW|DENY|ERR> [detail]
"""

import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# ---------------------------------------------------------------- config

DEFAULT_ALLOWED_SUFFIXES = (
    "daytona.io",
    "daytona.works",
    "wandb.ai",
    "huggingface.co",
    "hf.co",
    "openai.com",
    "api.llama.com",
)

ALLOWED_PORTS = {443, 80, 8443}

LISTEN_PORT = int(os.environ.get("EGRESS_PROXY_PORT", "3129"))
BIND_ADDR = os.environ.get("EGRESS_PROXY_BIND", "0.0.0.0")
LOG_PATH = os.environ.get(
    "EGRESS_PROXY_LOG",
    "/scratch/gpfs/TRIDAO/al9080/terminal-rl/logs/egress_proxy.log",
)

_extra = os.environ.get("EGRESS_PROXY_ALLOW", "")
ALLOWED_SUFFIXES = tuple(
    s.strip().lower().lstrip(".")
    for s in (list(DEFAULT_ALLOWED_SUFFIXES) + _extra.split(","))
    if s.strip()
)

HEADER_LIMIT = 65536          # max request-header bytes
CONNECT_TIMEOUT = 20          # seconds to establish upstream TCP
IDLE_TIMEOUT = 3600           # tear down a tunnel after 1h of silence
HEADER_READ_TIMEOUT = 30      # seconds to receive the request head

# ---------------------------------------------------------------- logging

log = logging.getLogger("egress_proxy")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
_fh = RotatingFileHandler(LOG_PATH, maxBytes=50 * 1024 * 1024, backupCount=3)
_fh.setFormatter(_fmt)
log.addHandler(_fh)

_sh = logging.StreamHandler(sys.stderr)   # journalctl --user -u egress-proxy
_sh.setFormatter(_fmt)
log.addHandler(_sh)

# ---------------------------------------------------------------- helpers


def host_allowed(host: str) -> bool:
    h = host.lower().rstrip(".")
    for d in ALLOWED_SUFFIXES:
        if h == d or h.endswith("." + d):
            return True
    return False


def split_hostport(target: str, default_port: int):
    """'host:port' or 'host' -> (host, port).  IPv6 literals unsupported."""
    if ":" in target:
        host, _, port_s = target.rpartition(":")
        try:
            return host, int(port_s)
        except ValueError:
            return target, default_port
    return target, default_port


async def _pipe(reader, writer):
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), timeout=IDLE_TIMEOUT)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def relay(cr, cw, ur, uw):
    await asyncio.gather(_pipe(cr, uw), _pipe(ur, cw))


def _resp(writer, status: str, body: bytes = b""):
    writer.write(
        (
            "HTTP/1.1 %s\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n" % (status, len(body))
        ).encode()
    )
    if body:
        writer.write(body)


# ---------------------------------------------------------------- handler


async def handle(client_reader, client_writer):
    peer = client_writer.get_extra_info("peername") or ("?", 0)
    src = "%s:%s" % (peer[0], peer[1])
    upstream_writer = None
    try:
        try:
            head = await asyncio.wait_for(
                client_reader.readuntil(b"\r\n\r\n"), timeout=HEADER_READ_TIMEOUT
            )
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return

        request_line, _, rest_headers = head.partition(b"\r\n")
        parts = request_line.decode("latin-1", "replace").split()
        if len(parts) != 3:
            _resp(client_writer, "400 Bad Request", b"malformed request line\n")
            await client_writer.drain()
            return
        method, target, _version = parts

        if method == "CONNECT":
            host, port = split_hostport(target, 443)
        elif target.lower().startswith("http://"):
            hostpart = target[7:].split("/", 1)[0]
            host, port = split_hostport(hostpart, 80)
        else:
            _resp(
                client_writer,
                "400 Bad Request",
                b"only CONNECT or absolute-form http:// requests accepted\n",
            )
            await client_writer.drain()
            log.info("src=%s method=%s dst=%s DENY non-proxy-request", src, method, target)
            return

        dst = "%s:%d" % (host, port)

        if not host_allowed(host) or port not in ALLOWED_PORTS:
            reason = "domain-not-allowlisted" if not host_allowed(host) else "port-not-allowed"
            log.info("src=%s method=%s dst=%s DENY %s", src, method, dst, reason)
            _resp(client_writer, "403 Forbidden",
                  ("egress to %s denied (%s)\n" % (dst, reason)).encode())
            await client_writer.drain()
            return

        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError) as e:
            log.info("src=%s method=%s dst=%s ERR upstream-connect: %s", src, method, dst, e)
            _resp(client_writer, "502 Bad Gateway",
                  ("could not reach %s: %s\n" % (dst, e)).encode())
            await client_writer.drain()
            return

        log.info("src=%s method=%s dst=%s ALLOW", src, method, dst)

        if method == "CONNECT":
            client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await client_writer.drain()
        else:
            # absolute-form -> origin-form; strip proxy headers; force close
            path = "/" + target[7:].split("/", 1)[1] if "/" in target[7:] else "/"
            out = ["%s %s HTTP/1.1" % (method, path)]
            for line in rest_headers.decode("latin-1", "replace").split("\r\n"):
                if not line:
                    continue
                key = line.split(":", 1)[0].strip().lower()
                if key in ("proxy-connection", "connection", "keep-alive"):
                    continue
                out.append(line)
            out.append("Connection: close")
            upstream_writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
            await upstream_writer.drain()

        await relay(client_reader, client_writer, upstream_reader, upstream_writer)

    except ConnectionError:
        pass
    except Exception as e:  # keep the service alive no matter what
        log.info("src=%s ERR handler: %r", src, e)
    finally:
        for w in (client_writer, upstream_writer):
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass


# ---------------------------------------------------------------- main


async def main():
    server = await asyncio.start_server(
        handle, BIND_ADDR, LISTEN_PORT, limit=HEADER_LIMIT
    )
    log.info(
        "egress proxy listening on %s:%d allow=%s ports=%s log=%s",
        BIND_ADDR, LISTEN_PORT, ",".join(ALLOWED_SUFFIXES),
        sorted(ALLOWED_PORTS), LOG_PATH,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
