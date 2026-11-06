import os
import hashlib


def test_result_file_exists():
    """Check that result.txt exists."""
    assert os.path.exists("/app/result.txt"), (
        "result.txt does not exist at /app/result.txt"
    )


def test_result_content():
    """Check that result.txt contains the correct model name."""
    with open("/app/result.txt", "r") as f:
        content = f.read().strip()

    assert len(content) > 0, "result.txt is empty"

    lines = content.split("\n")
    assert len(lines) == 1, (
        f"result.txt should contain exactly one line, got {len(lines)}: {lines}"
    )

    expected_hash = "4b37e85c8445700f06a52a032fd66f4976c7c006252c46870cee8fc702c2e600"
    actual_hash = hashlib.sha256(lines[0].encode()).hexdigest()
    assert actual_hash == expected_hash, (
        f"result.txt contains unexpected value: '{lines[0]}'"
    )
