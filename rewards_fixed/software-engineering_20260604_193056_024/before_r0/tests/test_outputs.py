import ctypes
import os
import subprocess
import sys


LIB_PATH = "/app/libcompress.so"


def _get_funcs():
    """Load libcompress.so and return (compress, decompress, compressBound)."""
    lib = ctypes.CDLL(LIB_PATH)

    cfn = lib.compress
    cfn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    cfn.restype = ctypes.c_int

    dfn = lib.decompress
    dfn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    dfn.restype = ctypes.c_int

    bfn = lib.compressBound
    bfn.argtypes = [ctypes.c_int]
    bfn.restype = ctypes.c_int

    return cfn, dfn, bfn


def _roundtrip(data: bytes) -> bytes:
    """Compress then decompress data, return the result."""
    cfn, dfn, bfn = _get_funcs()

    src = ctypes.create_string_buffer(data, len(data))
    bound = bfn(len(data))
    assert bound >= 0, f"compressBound({len(data)}) returned {bound}"
    dst = ctypes.create_string_buffer(max(bound, 1))

    compressed_size = cfn(src, dst, len(data))
    if len(data) == 0:
        assert compressed_size >= 0, (
            f"compress returned {compressed_size} for empty input"
        )
        if compressed_size == 0:
            return b""
    else:
        assert compressed_size > 0, (
            f"compress returned {compressed_size} for input of length {len(data)}"
        )

    compressed = dst.raw[:compressed_size]
    out = ctypes.create_string_buffer(max(len(data) * 2, 1))

    decompressed_size = dfn(compressed, out, compressed_size)
    assert decompressed_size == len(data), (
        f"decompress returned {decompressed_size}, expected {len(data)}"
    )

    return bytes(out.raw[:decompressed_size])


class TestLibraryPresent:
    """Verify the shared library exists and exports the required symbols."""

    def test_library_exists(self):
        assert os.path.exists(LIB_PATH), (
            f"Shared library not found at {LIB_PATH}. "
            f"Build it with: gcc -shared -fPIC -o libcompress.so compress.c"
        )

    def test_library_exports_symbols(self):
        lib = ctypes.CDLL(LIB_PATH)
        assert hasattr(lib, "compress"), "compress not exported"
        assert hasattr(lib, "decompress"), "decompress not exported"
        assert hasattr(lib, "compressBound"), "compressBound not exported"


class TestRoundtrip:
    """Test compress-then-decompress correctness on various inputs."""

    def test_empty_input(self):
        result = _roundtrip(b"")
        assert result == b"", f"Empty roundtrip failed: got {result!r}"

    def test_single_byte(self):
        result = _roundtrip(b"X")
        assert result == b"X", f"Single byte roundtrip failed: got {result!r}"

    def test_few_bytes(self):
        data = b"Hello, World!"
        result = _roundtrip(data)
        assert result == data, f"Few bytes roundtrip failed: got {result!r}"

    def test_repeated_pattern(self):
        data = b"ABCD" * 1000
        result = _roundtrip(data)
        assert result == data, (
            f"Repeated pattern roundtrip failed: length {len(result)} vs {len(data)}"
        )

    def test_incompressible_data(self):
        import random
        random.seed(12345)
        data = bytes(random.randint(0, 255) for _ in range(500))
        result = _roundtrip(data)
        assert result == data, (
            f"Incompressible data roundtrip failed: length {len(result)} vs {len(data)}"
        )

    def test_long_runs(self):
        data = b"A" * 10000
        result = _roundtrip(data)
        assert result == data, (
            f"Long run roundtrip failed: length {len(result)} vs {len(data)}"
        )

    def test_literal_boundary_sizes(self):
        for size in [14, 15, 16, 17, 254, 255, 256, 257, 510, 511, 512]:
            data = bytes(i % 256 for i in range(size))
            result = _roundtrip(data)
            assert result == data, (
                f"Literal boundary size {size} roundtrip failed: "
                f"length {len(result)} vs {len(data)}"
            )

    def test_data_with_matches_at_various_offsets(self):
        header = b"HEADER_DATA_"
        chunk = b"MATCH_THIS_PATTERN_1234567890"
        data = header + chunk + header + chunk + header + chunk + b"TAIL"
        result = _roundtrip(data)
        assert result == data, (
            f"Matches at various offsets roundtrip failed"
        )

    def test_binary_data(self):
        data = bytes(range(256)) * 3
        result = _roundtrip(data)
        assert result == data, (
            f"Binary data roundtrip failed: length {len(result)} vs {len(data)}"
        )


class TestLargeInput:
    """Test with larger inputs to verify the compressor handles them correctly."""

    def test_large_repeated(self):
        data = b"LARGE_PATTERN_ABCD" * 5000
        result = _roundtrip(data)
        assert result == data, (
            f"Large repeated roundtrip failed: length {len(result)} vs {len(data)}"
        )

    def test_large_randomish(self):
        import random
        random.seed(67890)
        data = bytes(random.randint(32, 126) for _ in range(20000))
        result = _roundtrip(data)
        assert result == data, (
            f"Large randomish roundtrip failed: length {len(result)} vs {len(data)}"
        )


class TestCompressionRatio:
    """Verify the compressor achieves reasonable compression and round-trips."""

    def test_highly_compressible_small(self):
        data = b"A" * 1000
        result = _roundtrip(data)
        assert result == data, "Round-trip failed"
        cfn, _, bfn = _get_funcs()
        src = ctypes.create_string_buffer(data, len(data))
        bound = bfn(len(data))
        dst = ctypes.create_string_buffer(bound)
        cs = cfn(src, dst, len(data))
        assert cs <= 25, (
            f"Compressed size {cs} for 1000 'A's is too large (expected <= 25). "
            f"The compressor is not finding matches effectively."
        )

    def test_highly_compressible_large(self):
        data = b"ABCD" * 5000
        result = _roundtrip(data)
        assert result == data, "Round-trip failed"
        cfn, _, bfn = _get_funcs()
        src = ctypes.create_string_buffer(data, len(data))
        bound = bfn(len(data))
        dst = ctypes.create_string_buffer(bound)
        cs = cfn(src, dst, len(data))
        assert cs <= 40, (
            f"Compressed size {cs} for 20000 bytes of repeated 'ABCD' "
            f"is too large (expected <= 40). "
            f"The compressor is not finding matches effectively."
        )
