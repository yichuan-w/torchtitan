#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py

import os
import pytest

SECRET_TXT_PATH = "/home/user/secret.txt"
EXPECTED_PLAINTEXT = "TELE_CRITICAL_SYSTEM_ALERT_FLAG{lcg_stream_broken_1337}"

def test_secret_txt_exists():
    """Verify that the secret.txt file exists."""
    assert os.path.exists(SECRET_TXT_PATH), f"Missing file: {SECRET_TXT_PATH}"
    assert os.path.isfile(SECRET_TXT_PATH), f"Not a file: {SECRET_TXT_PATH}"

def test_secret_txt_content():
    """Verify that the secret.txt file contains the correct decrypted plaintext."""
    with open(SECRET_TXT_PATH, "r", encoding="utf-8") as f:
        content = f.read().strip()

    assert content == EXPECTED_PLAINTEXT, f"The content of {SECRET_TXT_PATH} is incorrect. Expected: '{EXPECTED_PLAINTEXT}', Got: '{content}'"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
