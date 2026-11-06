#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
import hashlib
import os
import re
import subprocess
from pathlib import Path

from scapy.all import Ether, IP, Raw, UDP, wrpcap

SECRET_TXT_PATH = Path("/home/user/secret.txt")
SOLVE_RS_PATH = Path("/home/user/solve.rs")
TRAFFIC_PATH = Path("/home/user/traffic.bin")
EXPECTED_PLAINTEXT = "TELE_CRITICAL_SYSTEM_ALERT_FLAG{lcg_stream_broken_1337}"
EXPECTED_PACKET_CHECKSUM = bytes.fromhex(
    "dbc880d8937a797291dcfa939fb5e9da267238945e8b567f5786441498b7ad5e"
)


def test_secret_txt_exists():
    """Retained check: the required plaintext artifact exists as a file."""
    assert os.path.exists(SECRET_TXT_PATH), f"Missing file: {SECRET_TXT_PATH}"
    assert os.path.isfile(SECRET_TXT_PATH), f"Not a file: {SECRET_TXT_PATH}"


def test_secret_txt_content():
    """Retained check: the supplied capture's complete plaintext is exact."""
    content = SECRET_TXT_PATH.read_text(encoding="utf-8").strip()
    assert content == EXPECTED_PLAINTEXT, (
        f"The content of {SECRET_TXT_PATH} is incorrect. "
        f"Expected: '{EXPECTED_PLAINTEXT}', Got: '{content}'"
    )


def test_solve_rs_exists():
    """The newly specified Rust source artifact exists as a regular file."""
    assert os.path.exists(SOLVE_RS_PATH), f"Missing file: {SOLVE_RS_PATH}"
    assert os.path.isfile(SOLVE_RS_PATH), f"Not a file: {SOLVE_RS_PATH}"


def test_plaintext_matches_proprietary_checksum():
    """The recovered supplied plaintext matches the packet's embedded checksum."""
    plaintext = SECRET_TXT_PATH.read_text(encoding="utf-8").strip().encode()
    assert hashlib.sha256(plaintext).digest() == EXPECTED_PACKET_CHECKSUM


def _without_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def test_parameters_are_not_exhaustively_guessed():
    """Reject direct exhaustive iteration over both 8-bit parameter domains."""
    source = _without_comments(SOLVE_RS_PATH.read_text(encoding="utf-8"))
    domain = (
        r"\bfor\s+[A-Za-z_][A-Za-z0-9_]*\s+in\s+"
        r"(?:0(?:_?u(?:8|16|32|64|128|size))?\s*\.\.\s*(?:256|=\s*255)|"
        r"0\.\.256(?:_?u(?:8|16|32|64|128|size))?|"
        r"0\.\.=255(?:_?u(?:8|16|32|64|128|size))?)"
    )
    exhaustive_pair = re.compile(domain + r"[\s\S]{0,500}?" + domain, re.IGNORECASE)
    assert exhaustive_pair.search(source) is None, (
        "solve.rs exhaustively iterates over both 8-bit parameter domains; derive A and C algebraically"
    )


def _write_capture(plaintext, initial_state, multiplier, increment, path):
    checksum = hashlib.sha256(plaintext).digest()
    encrypted = bytearray()
    state = initial_state
    for byte in plaintext:
        encrypted.append(byte ^ state)
        state = (multiplier * state + increment) % 256
    packet = (
        Ether(src="02:aa:bb:cc:dd:01", dst="02:aa:bb:cc:dd:02")
        / IP(src="192.0.2.19", dst="203.0.113.88", id=0x7A31)
        / UDP(sport=31234, dport=4589)
        / Raw(checksum + encrypted)
    )
    packet.time = 1711111111.25
    wrpcap(str(path), [packet])


def test_compiled_source_reads_capture_reproduces_and_checks_checksum(tmp_path):
    """Compile the stated entry point and exercise valid and checksum-invalid captures."""
    executable = tmp_path / "submitted-solve"
    subprocess.run(
        ["rustc", "--edition=2021", str(SOLVE_RS_PATH), "-o", str(executable)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    original_capture = TRAFFIC_PATH.read_bytes()
    original_secret = SECRET_TXT_PATH.read_bytes()
    alternate_plaintext = b"TELE_DYNAMIC_CAPTURE_REPLAY_OK_2026"
    failures = []
    try:
        _write_capture(alternate_plaintext, 193, 29, 71, TRAFFIC_PATH)
        SECRET_TXT_PATH.unlink(missing_ok=True)
        valid_run = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=20
        )
        produced = SECRET_TXT_PATH.read_bytes() if SECRET_TXT_PATH.is_file() else None
        if valid_run.returncode != 0 or produced != alternate_plaintext:
            failures.append(
                "compiled solve.rs did not reproduce secret.txt from a different valid capture"
            )

        invalid_capture = bytearray(TRAFFIC_PATH.read_bytes())
        invalid_capture[-1] ^= 1
        TRAFFIC_PATH.write_bytes(invalid_capture)
        SECRET_TXT_PATH.unlink(missing_ok=True)
        subprocess.run([str(executable)], capture_output=True, text=True, timeout=20)
        if SECRET_TXT_PATH.exists():
            failures.append("solve.rs saved plaintext whose SHA-256 does not match the packet checksum")
    finally:
        TRAFFIC_PATH.write_bytes(original_capture)
        SECRET_TXT_PATH.write_bytes(original_secret)

    assert not failures, "; ".join(failures)
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
