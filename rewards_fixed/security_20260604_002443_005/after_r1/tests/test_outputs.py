import os
import re
import subprocess
from datetime import datetime


# Expected servers from inventory
SERVERS = [
    {"name": "app1", "hostname": "app1.prod.internal", "ip": "10.0.1.10", "key_type": "ed25519"},
    {"name": "app2", "hostname": "app2.prod.internal", "ip": "10.0.1.11", "key_type": "ed25519"},
    {"name": "db1",  "hostname": "db1.prod.internal",  "ip": "10.0.2.10", "key_type": "rsa"},
    {"name": "db2",  "hostname": "db2.prod.internal",  "ip": "10.0.2.11", "key_type": "ecdsa"},
    {"name": "monitor", "hostname": "monitor.prod.internal", "ip": "10.0.3.10", "key_type": "ed25519"},
]

REVOKED_SERVER = "db2"
ACTIVE_SERVERS = [s for s in SERVERS if s["name"] != REVOKED_SERVER]

SSH_CA_DIR = "/app/ssh-ca"
CA_KEY = f"{SSH_CA_DIR}/ca"
CA_PUB = f"{SSH_CA_DIR}/ca.pub"
KRL_FILE = f"{SSH_CA_DIR}/revoked.krl"


def _get_ca_fingerprint():
    """Get the SHA256 fingerprint of the CA public key."""
    proc = subprocess.run(
        ["ssh-keygen", "-l", "-f", CA_PUB],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, f"Failed to get CA fingerprint: {proc.stderr}"
    match = re.search(r"SHA256:([A-Za-z0-9+/=]+)", proc.stdout)
    assert match, f"Could not parse CA fingerprint from: {proc.stdout}"
    return match.group(1)


def _parse_cert_info(cert_path):
    """Run ssh-keygen -L on a certificate and return stdout."""
    proc = subprocess.run(
        ["ssh-keygen", "-L", "-f", cert_path],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, f"ssh-keygen -L failed for {cert_path}: {proc.stderr}"
    return proc.stdout


def _get_cert_serial(cert_path):
    """Extract the serial number from a certificate."""
    output = _parse_cert_info(cert_path)
    match = re.search(r"Serial:\s+(\d+)", output)
    assert match, f"Could not parse serial from {cert_path}: {output[:200]}"
    return int(match.group(1))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_directory_structure():
    """Verify the ssh-ca directory exists."""
    assert os.path.isdir(SSH_CA_DIR), f"Directory {SSH_CA_DIR} does not exist"


def test_ca_private_key():
    """Verify CA private key: exists, permissions 600, is ed25519."""
    assert os.path.exists(CA_KEY), f"CA private key {CA_KEY} does not exist"
    perms = os.stat(CA_KEY).st_mode & 0o777
    assert perms == 0o600, f"CA private key permissions: {perms:o}, expected 600"

    # Verify ed25519 via public key
    proc = subprocess.run(
        ["ssh-keygen", "-l", "-f", CA_PUB],
        capture_output=True, text=True
    )
    assert proc.returncode == 0, f"Failed to read CA public key: {proc.stderr}"
    assert "ED25519" in proc.stdout.upper(), (
        f"CA key is not ed25519: {proc.stdout}"
    )


def test_ca_public_key():
    """Verify CA public key exists and is ed25519 format."""
    assert os.path.exists(CA_PUB), f"CA public key {CA_PUB} does not exist"
    with open(CA_PUB, "r") as f:
        content = f.read()
    assert content.startswith("ssh-ed25519"), (
        f"CA public key not ed25519: {content[:60]}"
    )


def test_host_keys_exist():
    """Verify all host keys exist with correct types."""
    type_map = {
        "ed25519": "ssh-ed25519",
        "rsa": "ssh-rsa",
        "ecdsa": "ecdsa-sha2-",
    }
    for srv in SERVERS:
        key_path = f"{SSH_CA_DIR}/{srv['name']}_host_key"
        pub_path = f"{key_path}.pub"
        assert os.path.exists(key_path), f"Host key missing: {key_path}"
        assert os.path.exists(pub_path), f"Host public key missing: {pub_path}"

        with open(pub_path, "r") as f:
            pub_content = f.read()
        expected_prefix = type_map.get(srv["key_type"], srv["key_type"])
        assert pub_content.startswith(expected_prefix), (
            f"{srv['name']}: expected type {srv['key_type']}, "
            f"got prefix: {pub_content[:60]}"
        )


def test_host_certificates_exist():
    """Verify signed host certificates exist and are valid host certs."""
    for srv in SERVERS:
        cert_path = f"{SSH_CA_DIR}/{srv['name']}_host_key-cert.pub"
        assert os.path.exists(cert_path), f"Certificate missing: {cert_path}"

        proc = subprocess.run(
            ["ssh-keygen", "-L", "-f", cert_path],
            capture_output=True, text=True
        )
        assert proc.returncode == 0, (
            f"Certificate {cert_path} invalid: {proc.stderr}"
        )
        assert "host certificate" in proc.stdout.lower(), (
            f"Certificate {srv['name']} is not a host certificate"
        )


def test_certificate_principals():
    """Verify each certificate has correct principals."""
    for srv in SERVERS:
        cert_path = f"{SSH_CA_DIR}/{srv['name']}_host_key-cert.pub"
        output = _parse_cert_info(cert_path)
        for principal in [srv["hostname"], srv["name"], srv["ip"]]:
            assert principal in output, (
                f"{srv['name']}: missing principal '{principal}'"
            )


def test_certificate_identity():
    """Verify each certificate has the correct Key ID."""
    for srv in SERVERS:
        cert_path = f"{SSH_CA_DIR}/{srv['name']}_host_key-cert.pub"
        output = _parse_cert_info(cert_path)
        assert f'Key ID: "{srv["name"]}"' in output, (
            f"{srv['name']}: wrong Key ID, expected '{srv['name']}'"
        )


def test_certificate_ca_signature():
    """Verify all certificates are signed by the CA."""
    ca_fp = _get_ca_fingerprint()
    for srv in SERVERS:
        cert_path = f"{SSH_CA_DIR}/{srv['name']}_host_key-cert.pub"
        output = _parse_cert_info(cert_path)
        assert f"SHA256:{ca_fp}" in output, (
            f"{srv['name']}: not signed by CA (SHA256:{ca_fp})"
        )


def test_certificate_validity():
    """Verify each certificate is valid for ~52 weeks."""
    for srv in SERVERS:
        cert_path = f"{SSH_CA_DIR}/{srv['name']}_host_key-cert.pub"
        output = _parse_cert_info(cert_path)
        match = re.search(
            r"Valid:\s+from\s+(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}\s+"
            r"to\s+(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}",
            output
        )
        assert match, f"{srv['name']}: could not parse validity dates"
        from_date = datetime.strptime(match.group(1), "%Y-%m-%d")
        to_date = datetime.strptime(match.group(2), "%Y-%m-%d")
        delta = (to_date - from_date).days
        assert 358 <= delta <= 370, (
            f"{srv['name']}: validity {delta} days, expected ~364 (52 weeks)"
        )


def test_certificate_serial_numbers():
    """Verify certificates have sequential serial numbers starting from 1."""
    serials = {}
    for srv in SERVERS:
        cert_path = f"{SSH_CA_DIR}/{srv['name']}_host_key-cert.pub"
        serials[srv["name"]] = _get_cert_serial(cert_path)

    # All serials must be unique
    assert len(set(serials.values())) == len(SERVERS), (
        f"Duplicate serial numbers found: {serials}"
    )

    # Serials should be positive and sequential (some order, not necessarily
    # matching SERVERS order). The set should be {1, 2, 3, 4, 5}
    expected = set(range(1, len(SERVERS) + 1))
    actual = set(serials.values())
    assert actual == expected, (
        f"Serial numbers {sorted(actual)} != expected {sorted(expected)}. "
        f"Per-server: {serials}"
    )


def test_krl_exists():
    """Verify the Key Revocation List file exists."""
    assert os.path.exists(KRL_FILE), f"KRL missing: {KRL_FILE}"


def test_krl_revokes_db2():
    """Verify db2's certificate is in the KRL (revoked)."""
    db2_cert = f"{SSH_CA_DIR}/db2_host_key-cert.pub"
    proc = subprocess.run(
        ["ssh-keygen", "-Q", "-f", KRL_FILE, db2_cert],
        capture_output=True, text=True
    )
    # ssh-keygen -Q: exit 0 = key/cert NOT in KRL, exit 1 = IS in KRL (revoked)
    assert proc.returncode != 0, (
        f"db2 certificate NOT revoked (should be). "
        f"stdout: {proc.stdout}, stderr: {proc.stderr}"
    )


def test_krl_does_not_revoke_others():
    """Verify active servers' certificates are NOT in the KRL."""
    # First verify KRL exists (fail-fast if it doesn't, rather than
    # silently passing because ssh-keygen -Q fails on a missing file)
    assert os.path.exists(KRL_FILE), (
        f"KRL file {KRL_FILE} does not exist — cannot check revocation"
    )
    for srv in ACTIVE_SERVERS:
        cert_path = f"{SSH_CA_DIR}/{srv['name']}_host_key-cert.pub"
        proc = subprocess.run(
            ["ssh-keygen", "-Q", "-f", KRL_FILE, cert_path],
            capture_output=True, text=True
        )
        # ssh-keygen -Q: exit 0 = key/cert NOT in KRL, exit 1 = IS in KRL
        assert proc.returncode == 0, (
            f"{srv['name']} certificate incorrectly found in KRL "
            f"(should NOT be revoked). stdout: {proc.stdout}"
        )


def test_known_hosts():
    """Verify known_hosts has @cert-authority and @revoked directives."""
    kh_path = f"{SSH_CA_DIR}/known_hosts"
    assert os.path.exists(kh_path), f"known_hosts missing: {kh_path}"

    with open(kh_path, "r") as f:
        content = f.read()

    # @cert-authority line
    with open(CA_PUB, "r") as f:
        ca_pub = f.read().strip()
    ca_parts = ca_pub.split()
    assert len(ca_parts) >= 2, "CA public key malformed"
    ca_key_data = ca_parts[1]

    cert_auth_pattern = (
        r"@cert-authority\s+\*\.prod\.internal\s+ssh-ed25519\s+" + re.escape(ca_key_data)
    )
    assert re.search(cert_auth_pattern, content), (
        "known_hosts missing or malformed @cert-authority line"
    )

    # @revoked line referencing the KRL
    assert "@revoked" in content, "known_hosts missing @revoked directive"
    assert "revoked.krl" in content, (
        "known_hosts @revoked line does not reference revoked.krl"
    )
    revoke_pattern = r"@revoked\s+\*\.prod\.internal\s+.*revoked\.krl"
    assert re.search(revoke_pattern, content), (
        "known_hosts @revoked line has incorrect format"
    )


def test_generate_report_script():
    """Verify the report generation script exists and is executable."""
    script_path = f"{SSH_CA_DIR}/generate_report.sh"
    assert os.path.exists(script_path), (
        f"generate_report.sh missing: {script_path}"
    )
    assert os.access(script_path, os.X_OK), (
        f"generate_report.sh is not executable"
    )

    # Run it to produce verification.txt
    proc = subprocess.run(
        ["bash", script_path],
        capture_output=True, text=True,
        cwd=SSH_CA_DIR
    )
    assert proc.returncode == 0, (
        f"generate_report.sh failed: {proc.stderr}\n{proc.stdout}"
    )


def test_verification_report():
    """Verify the generated verification report has required content."""
    # First ensure generate_report.sh has been run
    script_path = f"{SSH_CA_DIR}/generate_report.sh"
    if os.path.exists(script_path):
        subprocess.run(
            ["bash", script_path],
            capture_output=True, text=True,
            cwd=SSH_CA_DIR
        )

    report_path = f"{SSH_CA_DIR}/verification.txt"
    assert os.path.exists(report_path), f"verification.txt missing: {report_path}"

    with open(report_path, "r") as f:
        content = f.read()

    ca_fp = _get_ca_fingerprint()

    # Report should mention active servers
    for srv in ACTIVE_SERVERS:
        assert srv["name"] in content, (
            f"Verification report missing server: {srv['name']}"
        )
        assert srv["hostname"] in content, (
            f"Verification report missing hostname for {srv['name']}"
        )
        # Should reference dates in YYYY-MM-DD format
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", content)
        assert len(dates) >= 2, (
            f"Verification report does not contain enough YYYY-MM-DD dates"
        )
        # Should reference CA verification
        assert ca_fp[:16] in content or "CA" in content, (
            f"Verification report missing CA fingerprint verification"
        )

    # Revoked server should be noted as revoked or excluded
    # (at minimum, it should not be listed as active/valid)
    # We don't assert it must be absent since the report could note it as revoked


def test_python_check_script():
    """Verify check_certs.py runs successfully and validates certs."""
    script_path = "/app/check_certs.py"
    assert os.path.exists(script_path), (
        f"check_certs.py missing: {script_path}"
    )

    proc = subprocess.run(
        ["python3", script_path],
        capture_output=True, text=True
    )

    assert proc.returncode == 0, (
        f"check_certs.py failed (exit {proc.returncode}):\n"
        f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    )

    # Should mention all servers
    for srv in SERVERS:
        assert srv["name"] in proc.stdout, (
            f"check_certs.py output missing server: {srv['name']}"
        )

    # Should indicate db2 is revoked
    assert "revok" in proc.stdout.lower(), (
        f"check_certs.py output does not mention revocation of db2:\n{proc.stdout}"
    )

    # Should indicate success
    assert any(w in proc.stdout.lower() for w in ["pass", "valid", "success", "ok"]), (
        f"check_certs.py output does not indicate success:\n{proc.stdout}"
    )
