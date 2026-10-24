"""Local plaintext -> mTLS relay to fwdproxy, for hosts where TTLS is off.

On MAST, ``http://fwdproxy:8080`` only works because the TTLS sidecar redirects
it to fwdproxy's mTLS port 8082 using the task's identity certificate. On Nebius
the TTLS sidecar cannot start alongside Monarch's job spec (MastLauncher.Config
.enable_ttls), so this relay does the same redirect in-process: it listens on
127.0.0.1:<port> and wraps each connection in TLS to fwdproxy:8082. Point
https_proxy/http_proxy at http://127.0.0.1:<port>.

    python fwdproxy_tls_relay.py [--port 18080]
"""

import argparse
import asyncio
import os
import ssl

_CERT = "/var/facebook/x509_identities/server.pem"
_CA = "/var/facebook/rootcanal/ca.pem"


def _tls_context() -> ssl.SSLContext:
    if os.path.exists(_CA):
        ctx = ssl.create_default_context(cafile=_CA)
        # fwdproxy's certificate names the service, not the "fwdproxy" alias.
        ctx.check_hostname = False
    else:
        # No internal CA bundle on the host: the system bundle cannot verify
        # fwdproxy's internal certificate, so authenticate one way (our client
        # certificate) as the probe did.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(_CERT)
    return ctx


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
        pass
    finally:
        writer.close()


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    *,
    upstream: tuple[str, int],
    ctx: ssl.SSLContext,
) -> None:
    try:
        up_reader, up_writer = await asyncio.open_connection(
            *upstream, ssl=ctx, server_hostname=upstream[0]
        )
    except OSError:
        client_writer.close()
        return
    await asyncio.gather(
        _pipe(client_reader, up_writer), _pipe(up_reader, client_writer)
    )


async def _serve(port: int, upstream: tuple[str, int]) -> None:
    ctx = _tls_context()
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, upstream=upstream, ctx=ctx), "127.0.0.1", port
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--upstream-host", default="fwdproxy")
    parser.add_argument("--upstream-port", type=int, default=8082)
    args = parser.parse_args()
    asyncio.run(_serve(args.port, (args.upstream_host, args.upstream_port)))


if __name__ == "__main__":
    main()
