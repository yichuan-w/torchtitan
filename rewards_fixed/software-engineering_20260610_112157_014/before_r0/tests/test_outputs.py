import os
import hashlib

RECOVERED_DIR = "/app/recovered"

EXPECTED_FILES = {
    "NOTES.TXT": {
        "size": 108,
        "sha256": "652c6fa6e9635e990237cbb61ddb8c888fd02de93b619158c466ee0f412794ae",
    },
    "CONFIG.DAT": {
        "size": 24,
        "sha256": "bb57ebf94e9515a33792984085bd68d2843b7fce118b3bd364a0e3e58067491a",
    },
    "SECRET.TXT": {
        "size": 74,
        "sha256": "c1b13367fbf35e94e4cba756faf8b0f15987209077d38645f77e7e992420ac2c",
    },
    "LOG.TXT": {
        "size": 8500,
        "sha256": "bdccd7628fae56cf22bd8d84ed4a8a5b829128d84b7619edd48577ad339ba51e",
    },
    "READ_DEV.TXT": {
        "size": 57,
        "sha256": "8183808422ae51cd77d0326d6f8e1b7d898a61182e3aa250daaf8784119f8f31",
    },
    "NOTEBACK.TXT": {
        "size": 55,
        "sha256": "1784057a59cb35edec1ea9f0543f2f273452fe84f302c0b39ef0fbe888ff61df",
    },
}

EXPECTED_CONTENT_CHECKS = {
    "NOTES.TXT": [
        (0, 14, b"Project Notes:"),
        (90, 108, b"are confidential.\n"),
    ],
    "CONFIG.DAT": [
        (0, 4, bytes([0x01, 0x00, 0x02, 0x00])),
        (8, 13, b"Hello"),
        (16, 24, bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0xBA, 0xBE])),
    ],
    "SECRET.TXT": [
        (0, 22, b"THE SECRET MESSAGE IS:"),
        (61, 74, b"with anyone.\n"),
    ],
    "LOG.TXT": [
        (0, 20, b"Line 0000: This is s"),
        (8460, 8500, b"ile and make it span multiple clusters.\n"),
    ],
    "READ_DEV.TXT": [
        (0, 19, b"Developer readme - "),
        (38, 57, b"Do not distribute.\n"),
    ],
    "NOTEBACK.TXT": [
        (0, 16, b"This is a backup"),
        (31, 55, b"Version 1.0 - Archived.\n"),
    ],
}

# Files that should NOT be in recovered/ (they were never deleted)
NON_DELETED_FILES = {"README.TXT", "DATA.BIN", "MAIN.C", "BUILD_SH.TXT"}


def test_recovered_dir_exists():
    """Check that /app/recovered/ directory exists."""
    assert os.path.isdir(RECOVERED_DIR), (
        f"Directory {RECOVERED_DIR} does not exist. "
        "Create it and place recovered files there."
    )


def test_all_files_recovered():
    """Check that all four deleted files were recovered."""
    missing = []
    for fname in EXPECTED_FILES:
        path = os.path.join(RECOVERED_DIR, fname)
        if not os.path.isfile(path):
            missing.append(fname)
    assert not missing, (
        f"Missing recovered files: {missing}. "
        f"All deleted files must be recovered and present in {RECOVERED_DIR}."
    )


def test_file_sizes():
    """Check that recovered files have the correct size."""
    for fname, info in EXPECTED_FILES.items():
        path = os.path.join(RECOVERED_DIR, fname)
        assert os.path.isfile(path), (
            f"File {fname} not found in {RECOVERED_DIR}. "
            f"All deleted files must be recovered."
        )
        actual_size = os.path.getsize(path)
        expected_size = info["size"]
        assert actual_size == expected_size, (
            f"File {fname}: expected size {expected_size} bytes, "
            f"got {actual_size} bytes."
        )


def test_file_content_checks():
    """Check that recovered files contain expected content at key positions."""
    for fname, checks in EXPECTED_CONTENT_CHECKS.items():
        path = os.path.join(RECOVERED_DIR, fname)
        assert os.path.isfile(path), (
            f"File {fname} not found in {RECOVERED_DIR}. "
            f"All deleted files must be recovered."
        )
        
        with open(path, 'rb') as f:
            content = f.read()
        
        for start, end, expected in checks:
            actual = content[start:end]
            assert actual == expected, (
                f"File {fname}: content mismatch at bytes [{start}:{end}]. "
                f"Expected {expected!r}, got {actual!r}."
            )


def test_no_extra_files():
    """Check that only the expected deleted files are in recovered/, not non-deleted ones."""
    assert os.path.isdir(RECOVERED_DIR), (
        f"Directory {RECOVERED_DIR} does not exist. "
        "Create it and place recovered files there."
    )
    files = set(f for f in os.listdir(RECOVERED_DIR) 
                if os.path.isfile(os.path.join(RECOVERED_DIR, f)))
    
    # Check that non-deleted files are NOT in recovered/
    non_deleted_found = files & NON_DELETED_FILES
    assert not non_deleted_found, (
        f"Non-deleted files found in {RECOVERED_DIR}: {non_deleted_found}. "
        f"These files were never deleted and should not be recovered."
    )
    
    # Check for unexpected files (not in expected AND not in non-deleted)
    unexpected = files - set(EXPECTED_FILES.keys()) - NON_DELETED_FILES
    assert not unexpected, (
        f"Unexpected files in {RECOVERED_DIR}: {unexpected}. "
        f"Only recover the deleted files."
    )