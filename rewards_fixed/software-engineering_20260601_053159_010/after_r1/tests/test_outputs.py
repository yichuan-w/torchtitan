import subprocess
import os
from pathlib import Path


def _compile():
    """Compile huff.c and return the path to the binary."""
    src = Path("/app/huff.c")
    assert src.exists(), "/app/huff.c does not exist"
    result = subprocess.run(
        ["gcc", "-O3", "-lm", "-o", "/app/huff", str(src)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Compilation failed: {result.stderr}"
    return "/app/huff"


def _run_huff(mode, input_path, output_path):
    """Run the huff binary and check for success."""
    result = subprocess.run(
        ["/app/huff", mode, input_path, output_path],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"huff {mode} failed on {input_path}: {result.stderr}"
    )
    return result


def _roundtrip(filename, orig_path=None):
    """Compress and decompress a file, verify round-trip correctness."""
    if orig_path is None:
        orig_path = f"/app/{filename}"
    compressed = f"/tmp/{filename}.huff"
    decompressed = f"/tmp/{filename}.out"

    # Clean up from any previous runs
    for p in [compressed, decompressed]:
        if os.path.exists(p):
            os.remove(p)

    _run_huff("c", orig_path, compressed)
    _run_huff("d", compressed, decompressed)

    with open(orig_path, "rb") as f:
        original = f.read()
    with open(decompressed, "rb") as f:
        result_data = f.read()

    assert original == result_data, (
        f"Round-trip mismatch for {filename}: "
        f"original={len(original)} bytes, result={len(result_data)} bytes"
    )


def _compressed_smaller(filename, orig_path=None):
    """Verify that compression actually reduces size for compressible files."""
    if orig_path is None:
        orig_path = f"/app/{filename}"
    compressed = f"/tmp/{filename}.huff"

    if os.path.exists(compressed):
        os.remove(compressed)

    _run_huff("c", orig_path, compressed)

    orig_size = os.path.getsize(orig_path)
    comp_size = os.path.getsize(compressed)

    assert comp_size < orig_size, (
        f"Compressed file for {filename} is not smaller: "
        f"{comp_size} >= {orig_size}"
    )


def test_huff_c_exists():
    """Test that the requested source file exists."""
    src = Path("/app/huff.c")
    assert src.exists(), "/app/huff.c does not exist"


def test_huff_compiles():
    """Test that huff.c compiles successfully."""
    _compile()


def test_roundtrip_alice():
    """Test round-trip on natural English text."""
    _compile()
    _roundtrip("alice.txt")


def test_roundtrip_alphabet_repeat():
    """Test round-trip on repeating alphabet pattern."""
    _compile()
    _roundtrip("alphabet_repeat.txt")


def test_roundtrip_random_text():
    """Test round-trip on random printable text."""
    _compile()
    _roundtrip("random_text.txt")


def test_roundtrip_single_char():
    """Test round-trip on file with a single repeated character."""
    _compile()
    _roundtrip("single_char.txt")


def test_roundtrip_empty():
    """Test round-trip on an empty file."""
    _compile()
    _roundtrip("empty.txt")


def test_roundtrip_dna():
    """Test round-trip on DNA-like sequence (only 4 characters)."""
    _compile()
    _roundtrip("dna.txt")


def test_compression_ratio_alphabet():
    """Test that repeating alphabet compresses significantly."""
    _compile()
    _compressed_smaller("alphabet_repeat.txt")


def test_compression_ratio_single_char():
    """Test that single repeated character compresses significantly."""
    _compile()
    _compressed_smaller("single_char.txt")


def test_compression_ratio_dna():
    """Test that DNA-like sequence compresses."""
    _compile()
    _compressed_smaller("dna.txt")


def test_empty_file_handling():
    """Test that empty file produces a valid compressed file that can be decompressed."""
    _compile()
    compressed = "/tmp/empty_test.huff"
    decompressed = "/tmp/empty_test.out"

    for p in [compressed, decompressed]:
        if os.path.exists(p):
            os.remove(p)

    _run_huff("c", "/app/empty.txt", compressed)
    assert os.path.exists(compressed), "Compressed empty file was not created"
    _run_huff("d", compressed, decompressed)

    with open("/app/empty.txt", "rb") as f:
        original = f.read()
    with open(decompressed, "rb") as f:
        result = f.read()

    assert original == result, "Empty file round-trip failed"

