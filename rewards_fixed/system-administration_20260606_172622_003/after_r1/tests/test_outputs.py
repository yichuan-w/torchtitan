import datetime
import glob
import os
import re
import shutil
import socket
import subprocess
import time
import uuid


def run(command, *, input_text=None, timeout=20):
    return subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=timeout)


def wait_for_text(text, path=None, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        paths = [path] if path else glob.glob("/var/log/remote/**/syslog.log", recursive=True)
        for candidate in paths:
            try:
                if text in open(candidate, errors="replace").read():
                    return candidate
            except OSError:
                pass
        time.sleep(0.5)
    return None


def certificate_inventory():
    certificates = []
    for path in glob.glob("/etc/rsyslog/certs/*"):
        result = run(["certtool", "--certificate-info", "--infile", path])
        if result.returncode == 0:
            certificates.append((path, result.stdout + result.stderr))
    return certificates


def ca_path():
    for path, details in certificate_inventory():
        if re.search(r"Certificate Authority \(CA\):\s*TRUE", details, re.IGNORECASE):
            return path
    return None


def restart_rsyslog():
    run(["pkill", "-TERM", "rsyslogd"])
    deadline = time.time() + 5
    while time.time() < deadline and run(["pgrep", "rsyslogd"]).returncode == 0:
        time.sleep(0.1)
    return subprocess.Popen(["rsyslogd", "-n"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def expected_remote_path():
    now = datetime.datetime.now()
    return f"/var/log/remote/{socket.gethostname()}/{now:%Y/%m/%d}/syslog.log"


def test_certificates():
    inventory = certificate_inventory()
    cas = [(path, info) for path, info in inventory if re.search(r"Certificate Authority \(CA\):\s*TRUE", info, re.I)]
    hosts = [(path, info) for path, info in inventory if re.search(r"Subject:\s*CN=localhost(?:\s|,|$)", info, re.I) and not re.search(r"Certificate Authority \(CA\):\s*TRUE", info, re.I)]
    valid_chain = any(run(["certtool", "--verify", "--load-ca-certificate", ca, "--infile", host]).returncode == 0 for ca, _ in cas for host, _ in hosts)
    assert inventory and cas and hosts and valid_chain, "Expected a CA certificate and a CA-signed host certificate with CN localhost under /etc/rsyslog/certs"


def test_tls_receiver_and_client_trust():
    ca = ca_path()
    result = run(["openssl", "s_client", "-connect", "localhost:6514", "-servername", "localhost", "-verify_hostname", "localhost", "-verify_return_error", "-CAfile", ca or "/nonexistent"], input_text="", timeout=15)
    output = result.stdout + result.stderr
    verified_server = result.returncode == 0 and "Verify return code: 0 (ok)" in output and "BEGIN CERTIFICATE" in output
    original_ca = open(ca, "rb").read() if ca else b""
    marker = f"UNTRUSTED_RECEIVER_{uuid.uuid4().hex}"
    rejected_untrusted = False
    try:
        if ca:
            run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=Untrusted Test CA", "-keyout", "/tmp/untrusted-ca.key", "-out", "/tmp/untrusted-ca.pem", "-days", "1"])
            shutil.copyfile("/tmp/untrusted-ca.pem", ca)
            restart_rsyslog()
            time.sleep(1)
            run(["logger", marker])
            rejected_untrusted = wait_for_text(marker, expected_remote_path(), timeout=3) is None
    finally:
        if ca and original_ca:
            with open(ca, "wb") as handle:
                handle.write(original_ca)
            restart_rsyslog()
            time.sleep(1)
    assert verified_server and rejected_untrusted, f"The receiver must present a CA-verifiable localhost certificate and its forwarding client must reject an untrusted receiver: {output[-800:]}"


def test_local_forwarding():
    marker = f"LOCAL_FORWARD_{uuid.uuid4().hex}"
    sent = run(["logger", "-p", "local0.info", marker])
    path = wait_for_text(marker, expected_remote_path())
    assert sent.returncode == 0 and path, f"A locally generated message was not stored at {expected_remote_path()}"


def test_imfile_tag_and_facility():
    original = open("/etc/rsyslog.conf").read()
    diagnostic = f"/tmp/imfile-facility-{uuid.uuid4().hex}"
    marker = f"APP_LINE_{uuid.uuid4().hex}"
    rule = f'''\nif ($programname startswith "app-access" and $syslogfacility-text == "local1") then action(type="omfile" file="{diagnostic}")\n'''
    try:
        with open("/etc/rsyslog.conf", "w") as handle:
            handle.write(rule + original)
        restart_rsyslog()
        os.makedirs("/var/log/app", exist_ok=True)
        with open("/var/log/app/access.log", "a") as handle:
            handle.write(marker + "\n")
        remote = wait_for_text(marker)
        classified = wait_for_text(marker, diagnostic)
        remote_text = open(remote, errors="replace").read() if remote else ""
    finally:
        with open("/etc/rsyslog.conf", "w") as handle:
            handle.write(original)
        restart_rsyslog()
    assert remote and "app-access" in remote_text and classified, "A new access.log line was not forwarded with tag app-access and facility local1"


def test_log_rotation_policy():
    matching_stanzas = []
    candidates = ["/etc/logrotate.conf"] + glob.glob("/etc/logrotate.d/*")
    for candidate in candidates:
        try:
            text = open(candidate).read()
        except OSError:
            continue
        for header, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text, re.S):
            if "/var/log/remote/" in header or "/var/log/remote/" in body:
                matching_stanzas.append(header + "{" + body + "}")
    valid = any(re.search(r"(?m)^\s*daily\s*$", stanza) and re.search(r"(?m)^\s*rotate\s+7\s*$", stanza) and re.search(r"(?m)^\s*compress\s*$", stanza) for stanza in matching_stanzas)
    assert valid, "Remote logs must have a logrotate stanza specifying daily, rotate 7, and gzip compression"


def test_survives_restart():
    restart_rsyslog()
    time.sleep(1)
    marker = f"AFTER_RESTART_{uuid.uuid4().hex}"
    run(["logger", marker])
    path = wait_for_text(marker, expected_remote_path())
    assert path == expected_remote_path(), f"Local TLS forwarding did not continue after restart into {expected_remote_path()}"


def test_received_messages_do_not_loop():
    marker = f"NO_LOOP_{uuid.uuid4().hex}"
    run(["logger", marker])
    path = wait_for_text(marker, expected_remote_path())
    time.sleep(3)
    count = open(path, errors="replace").read().count(marker) if path else 0
    assert count == 1, f"The receiver stored the test message {count} times; received messages must not be re-forwarded"
