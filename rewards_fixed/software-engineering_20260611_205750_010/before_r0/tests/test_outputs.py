import subprocess
import zlib
import os
from pathlib import Path


def test_deflate_c_exists_and_size():
    """Check that deflate.c exists and is under 5000 bytes."""
    src = Path("/app/deflate.c")
    assert src.exists(), f"File {src} does not exist"
    size = src.stat().st_size
    assert size < 5000, f"deflate.c is {size} bytes, must be <5000"


def test_deflate_c_compiles():
    """Compile deflate.c with gcc -O3 -lm."""
    result = subprocess.run(
        ["gcc", "-O3", "-lm", "/app/deflate.c"],
        capture_output=True, text=True, cwd="/app",
    )
    assert result.returncode == 0, f"Compilation failed:\n{result.stderr}"


def _run_decompressor(compressed_data: bytes) -> bytes:
    """Run the compiled decompressor on the given compressed data."""
    result = subprocess.run(
        ["/app/a.out"],
        input=compressed_data,
        capture_output=True, text=False,
        timeout=30, cwd="/app",
    )
    assert result.returncode == 0, (
        f"Decompressor exited with code {result.returncode}: {result.stderr.decode(errors='replace')}"
    )
    return result.stdout


def test_decompress_simple_repeated():
    """Decompress a simple repeated pattern."""
    original = b"The quick brown fox jumps over the lazy dog. " * 10
    compressed = zlib.compress(original, level=6)
    output = _run_decompressor(compressed)
    assert output == original, (
        f"Decompression mismatch for repeated pattern. "
        f"Expected {len(original)} bytes, got {len(output)} bytes."
    )


def test_decompress_highly_compressible():
    """Decompress highly compressible data (all same byte)."""
    original = b"A" * 1000
    compressed = zlib.compress(original, level=9)
    output = _run_decompressor(compressed)
    assert output == original, (
        f"Decompression mismatch for uniform data. "
        f"Expected {len(original)} bytes, got {len(output)} bytes."
    )


def test_decompress_randomish():
    """Decompress data that is somewhat random (harder to compress)."""
    import random
    random.seed(42)
    original = bytes(random.randint(0, 255) for _ in range(500))
    compressed = zlib.compress(original, level=6)
    output = _run_decompressor(compressed)
    assert output == original, (
        f"Decompression mismatch for random-ish data. "
        f"Expected {len(original)} bytes, got {len(output)} bytes."
    )


def test_decompress_short():
    """Decompress a very short message."""
    original = b"Hi!"
    compressed = zlib.compress(original, level=6)
    output = _run_decompressor(compressed)
    assert output == original, (
        f"Decompression mismatch for short data. "
        f"Expected {original!r}, got {output!r}."
    )


def test_decompress_binary_data():
    """Decompress binary data with all byte values."""
    original = bytes(range(256)) * 3  # 768 bytes covering all byte values
    compressed = zlib.compress(original, level=6)
    output = _run_decompressor(compressed)
    assert output == original, (
        f"Decompression mismatch for binary data. "
        f"Expected {len(original)} bytes, got {len(output)} bytes."
    )


def test_decompress_sample_file():
    """Decompress the provided sample.zlib file."""
    sample_path = Path("/app/sample.zlib")
    assert sample_path.exists(), "sample.zlib not found"
    compressed = sample_path.read_bytes()
    output = _run_decompressor(compressed)
    # Verify it decompresses to valid text containing expected content
    text = output.decode("utf-8", errors="replace")
    assert "Congratulations" in text, (
        f"Sample decompression produced unexpected output: {text[:200]}"
    )
    assert "DEFLATE" in text, (
        "Sample decompression missing expected content"
    )


def test_decompress_larger_payload():
    """Decompress a larger payload to stress-test the implementation."""
    import random
    random.seed(12345)
    # Create a mix of compressible and random data
    parts = []
    for i in range(20):
        if i % 2 == 0:
            parts.append(b"Hello World! " * 20)
        else:
            parts.append(bytes(random.randint(32, 126) for _ in range(100)))
    original = b"\n".join(parts)
    compressed = zlib.compress(original, level=6)
    output = _run_decompressor(compressed)
    assert output == original, (
        f"Decompression mismatch for larger payload. "
        f"Expected {len(original)} bytes, got {len(output)} bytes."
    )