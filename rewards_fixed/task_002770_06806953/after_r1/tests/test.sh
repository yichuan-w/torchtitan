#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py

import os
import subprocess
import re
import pytest

SCRIPT_PATH = "/home/user/setup_local_proxy.sh"
CERTS_DIR = "/home/user/certs"
CERT_PATH = os.path.join(CERTS_DIR, "cert.pem")
KEY_PATH = os.path.join(CERTS_DIR, "key.pem")
PROXY_CONF = "/home/user/proxy.conf"
DEPLOY_LOG = "/home/user/deploy.log"

def test_script_exists_and_executable():
    assert os.path.isfile(SCRIPT_PATH), f"Script not found at {SCRIPT_PATH}"
    assert os.access(SCRIPT_PATH, os.X_OK), f"Script at {SCRIPT_PATH} is not executable"

def test_run_script_and_validate_output():
    # Run the script with input "staging"
    process = subprocess.Popen(
        [SCRIPT_PATH],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate(input="staging\n")

    assert process.returncode == 0, f"Script execution failed with return code {process.returncode}. Stderr: {stderr}"

    # Check if the prompt was printed (might be in stdout or stderr)
    output = stdout + stderr
    assert "Enter deployment tier: " in output, "Script did not prompt exactly with 'Enter deployment tier: '"

def test_certs_generated():
    assert os.path.isdir(CERTS_DIR), f"Certs directory not created at {CERTS_DIR}"
    assert os.path.isfile(CERT_PATH), f"Certificate not found at {CERT_PATH}"
    assert os.path.isfile(KEY_PATH), f"Private key not found at {KEY_PATH}"

def test_nginx_config_validity():
    assert os.path.isfile(PROXY_CONF), f"Nginx config not found at {PROXY_CONF}"

    # Run nginx config test
    result = subprocess.run(
        ["nginx", "-t", "-c", PROXY_CONF],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    assert result.returncode == 0, f"Nginx configuration test failed. Stderr: {result.stderr}"

def test_nginx_config_contents():
    with open(PROXY_CONF, "r") as f:
        content = f.read()

    assert "events" in content, "Config missing 'events' block"
    assert "http" in content, "Config missing 'http' block"
    assert "server" in content, "Config missing 'server' block"
    assert re.search(r"pid\s+/home/user/nginx\.pid\s*;", content), "Config missing or incorrect 'pid' directive"
    assert re.search(r"error_log\s+/home/user/error\.log\s*;", content), "Config missing or incorrect 'error_log' directive"
    assert re.search(r"access_log\s+/home/user/access\.log\s*;", content), "Config missing or incorrect 'access_log' directive"
    assert re.search(r"listen\s+127\.0\.0\.1:8443\s+ssl\s*;", content), "Config missing or incorrect 'listen' directive for 127.0.0.1:8443 ssl"
    assert re.search(r"ssl_certificate\s+/home/user/certs/cert\.pem\s*;", content), "Config missing or incorrect 'ssl_certificate' directive"
    assert re.search(r"ssl_certificate_key\s+/home/user/certs/key\.pem\s*;", content), "Config missing or incorrect 'ssl_certificate_key' directive"
    assert re.search(r"proxy_pass\s+http://127\.0\.0\.1:8080/?\s*;", content), "Config missing or incorrect 'proxy_pass' directive"

def test_deploy_log():
    assert os.path.isfile(DEPLOY_LOG), f"Deploy log not found at {DEPLOY_LOG}"

    with open(DEPLOY_LOG, "r") as f:
        lines = f.read().splitlines()

    assert len(lines) >= 1, "Deploy log is empty"
    last_line = lines[-1]

    match = re.match(r"^Tier: staging, Cert SHA256: (([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2})$", last_line)
    assert match, f"Log line does not match expected format: {last_line}"

    logged_fingerprint = match.group(1)

    # Get actual fingerprint
    result = subprocess.run(
        ["openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", CERT_PATH],
        stdout=subprocess.PIPE,
        text=True,
        check=True
    )
    actual_fingerprint_full = result.stdout.strip()
    assert "=" in actual_fingerprint_full, f"Unexpected openssl output: {actual_fingerprint_full}"
    actual_fingerprint = actual_fingerprint_full.split("=")[1]

    assert logged_fingerprint == actual_fingerprint, f"Logged fingerprint {logged_fingerprint} does not match actual fingerprint {actual_fingerprint}"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
