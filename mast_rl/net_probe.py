"""Print what a MAST host can reach: identity certs, fwdproxy ports, and an
HTTPS request through a TLS-wrapped fwdproxy CONNECT. Run on the controller by
run.sh when MAST_RL_NET_PROBE=1; output lands in the controller console log."""

import glob
import os
import socket
import ssl


def tcp(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=10):
            return "open"
    except OSError as e:
        return f"FAIL {e}"


def main() -> None:
    print("== certs")
    for pattern in (
        "/var/facebook/x509_identities/*",
        "/var/facebook/credentials/*/x509/*",
        "/var/facebook/ttls/*",
    ):
        for path in glob.glob(pattern):
            print(path, os.path.getsize(path) if os.path.isfile(path) else "dir")
    print("== resolve fwdproxy")
    try:
        print(socket.getaddrinfo("fwdproxy", 8080)[:2])
    except OSError as e:
        print("FAIL", e)
    for port in (8080, 8082):
        print(f"fwdproxy:{port}", tcp("fwdproxy", port))

    cert = "/var/facebook/x509_identities/server.pem"
    if not os.path.exists(cert):
        print("no identity cert at", cert)
        return
    ctx = ssl.create_default_context(cafile="/var/facebook/rootcanal/ca.pem")
    if not os.path.exists("/var/facebook/rootcanal/ca.pem"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(cert)
    try:
        raw = socket.create_connection(("fwdproxy", 8082), timeout=15)
        tls = ctx.wrap_socket(raw, server_hostname="fwdproxy")
        tls.sendall(b"CONNECT huggingface.co:443 HTTP/1.1\r\nHost: huggingface.co:443\r\n\r\n")
        print("CONNECT via TLS fwdproxy:8082 ->", tls.recv(200).split(b"\r\n")[0])
        tls.close()
    except Exception as e:
        print("TLS fwdproxy:8082 FAIL", type(e).__name__, e)


if __name__ == "__main__":
    main()
