"""Tests for the SBMF parser task.

Verifies that the parser correctly decodes all test files by comparing
its output against a reference Python decoder.
"""

import struct
import subprocess
from pathlib import Path


# --- Reference SBMF decoder (must match format-spec.txt) ---

def _decode_value(data, pos=0):
    """Reference decoder for SBMF format. Returns (value, new_pos)."""
    tag = data[pos]
    pos += 1

    # Positive fixint: 0x00 - 0x7F
    if tag <= 0x7F:
        return tag, pos

    # Negative fixint: 0x80 - 0x9F
    if 0x80 <= tag <= 0x9F:
        return -(tag & 0x1F) - 1, pos

    if tag == 0xA0:
        return None, pos
    if tag == 0xA1:
        return False, pos
    if tag == 0xA2:
        return True, pos

    # uint8
    if tag == 0xA8:
        return data[pos], pos + 1

    # uint16
    if tag == 0xA9:
        return struct.unpack('>H', data[pos:pos+2])[0], pos + 2

    # uint32
    if tag == 0xAA:
        return struct.unpack('>I', data[pos:pos+4])[0], pos + 4

    # uint64
    if tag == 0xAB:
        return struct.unpack('>Q', data[pos:pos+8])[0], pos + 8

    # int8
    if tag == 0xAC:
        return struct.unpack('>b', data[pos:pos+1])[0], pos + 1

    # int16
    if tag == 0xAD:
        return struct.unpack('>h', data[pos:pos+2])[0], pos + 2

    # int32
    if tag == 0xAE:
        return struct.unpack('>i', data[pos:pos+4])[0], pos + 4

    # int64
    if tag == 0xAF:
        return struct.unpack('>q', data[pos:pos+8])[0], pos + 8

    # float32
    if tag == 0xB0:
        bits = struct.unpack('>I', data[pos:pos+4])[0]
        val = struct.unpack('>f', struct.pack('>I', bits))[0]
        return val, pos + 4

    # float64
    if tag == 0xB1:
        bits = struct.unpack('>Q', data[pos:pos+8])[0]
        val = struct.unpack('>d', struct.pack('>Q', bits))[0]
        return val, pos + 8

    # string (1-byte len)
    if tag == 0xC0:
        length = data[pos]
        pos += 1
        return data[pos:pos+length].decode('utf-8'), pos + length

    # string (2-byte len)
    if tag == 0xC1:
        length = struct.unpack('>H', data[pos:pos+2])[0]
        pos += 2
        return data[pos:pos+length].decode('utf-8'), pos + length

    # array (1-byte count)
    if tag == 0xD0:
        count = data[pos]
        pos += 1
        result = []
        for _ in range(count):
            item, pos = _decode_value(data, pos)
            result.append(item)
        return result, pos

    # array (2-byte count)
    if tag == 0xD1:
        count = struct.unpack('>H', data[pos:pos+2])[0]
        pos += 2
        result = []
        for _ in range(count):
            item, pos = _decode_value(data, pos)
            result.append(item)
        return result, pos

    # map (1-byte count)
    if tag == 0xE0:
        count = data[pos]
        pos += 1
        result = {}
        for _ in range(count):
            k, pos = _decode_value(data, pos)
            v, pos = _decode_value(data, pos)
            result[k] = v
        return result, pos

    # map (2-byte count)
    if tag == 0xE1:
        count = struct.unpack('>H', data[pos:pos+2])[0]
        pos += 2
        result = {}
        for _ in range(count):
            k, pos = _decode_value(data, pos)
            v, pos = _decode_value(data, pos)
            result[k] = v
        return result, pos

    raise ValueError(f"Unknown tag: 0x{tag:02X}")


def ref_decode(data):
    """Decode an SBMF message. Returns the decoded value."""
    val, pos = _decode_value(data, 0)
    if pos != len(data):
        raise ValueError(f"Extra bytes after message: {len(data) - pos}")
    return val


def _parse_parser_output(text):
    """Parse the parser's text output back into a Python value.

    This is a simplified parser that handles the exact format the C parser produces.
    """
    lines = text.strip().split('\n')
    return _parse_lines(lines, 0)[0]


def _parse_lines(lines, pos):
    """Parse lines starting at pos. Returns (value, new_pos)."""
    line = lines[pos].rstrip()
    pos += 1

    # Strip indentation
    stripped = line.lstrip()

    if stripped == "null":
        return None, pos
    if stripped == "true":
        return True, pos
    if stripped == "false":
        return False, pos

    if stripped == "[":
        result = []
        while pos < len(lines):
            next_stripped = lines[pos].lstrip()
            if next_stripped == "]":
                pos += 1
                break
            item, pos = _parse_lines(lines, pos)
            result.append(item)
        return result, pos

    if stripped == "{":
        result = {}
        while pos < len(lines):
            next_stripped = lines[pos].lstrip()
            if next_stripped == "}":
                pos += 1
                break
            key, pos = _parse_lines(lines, pos)
            val, pos = _parse_lines(lines, pos)
            result[key] = val
        return result, pos

    if stripped.startswith('"') and stripped.endswith('"'):
        return stripped[1:-1], pos

    # Try integer
    try:
        return int(stripped), pos
    except ValueError:
        pass

    # Try float
    try:
        return float(stripped), pos
    except ValueError:
        pass

    raise ValueError(f"Cannot parse line: {stripped!r}")


def _approx_equal(a, b):
    """Deep comparison allowing for float32 precision loss and int/float mixing."""
    # Allow int/float mixing (the parser's %.9g drops trailing .0)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        fa, fb = float(a), float(b)
        if fa == 0.0 or fb == 0.0:
            return abs(fa - fb) < 1e-5
        return abs(fa - fb) / max(abs(fa), abs(fb)) < 1e-5

    if type(a) != type(b):
        return False
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_approx_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        if len(a) != len(b):
            return False
        return all(k in b and _approx_equal(a[k], b[k]) for k in a)
    return a == b


# --- Tests ---

TESTDATA_DIR = Path("/app/testdata")
PARSER_SRC = Path("/app/parser.c")
PARSER_BIN = Path("/app/parser")


def _build_parser():
    """Build the parser and return the path to the binary."""
    PARSER_BIN.unlink(missing_ok=True)
    result = subprocess.run(
        ["gcc", "-Wall", "-o", str(PARSER_BIN), str(PARSER_SRC)],
        capture_output=True, text=True, cwd="/app",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Parser failed to compile.\n"
            f"stderr: {result.stderr}\n"
            f"stdout: {result.stdout}"
        )
    if not PARSER_BIN.exists():
        raise RuntimeError("Parser binary was not created after compilation")
    return PARSER_BIN


def test_all_messages_decode_correctly():
    """Verify the parser correctly decodes every test file.

    Compiles parser.c, runs it against all .sbmf files in /app/testdata/,
    and compares the output against a reference Python decoder that
    implements the SBMF specification.
    """
    parser = _build_parser()

    test_files = sorted(TESTDATA_DIR.glob("*.sbmf"))
    assert len(test_files) > 0, "No test files found in /app/testdata/"

    errors = []
    passed = 0

    for tf in test_files:
        # Read the binary file
        with open(tf, "rb") as f:
            data = f.read()

        # Reference decode
        ref_val = ref_decode(data)

        # Run the parser
        result = subprocess.run(
            [str(parser), str(tf)],
            capture_output=True, text=True, timeout=10,
        )

        if result.returncode != 0:
            errors.append(
                f"{tf.name}: parser exited with code {result.returncode}\n"
                f"  stderr: {result.stderr}"
            )
            continue

        # Parse the parser's output
        try:
            parsed_val = _parse_parser_output(result.stdout)
        except Exception as e:
            errors.append(
                f"{tf.name}: failed to parse output: {e}\n"
                f"  parser output:\n{result.stdout}"
            )
            continue

        # Compare
        if not _approx_equal(parsed_val, ref_val):
            errors.append(
                f"{tf.name}: value mismatch\n"
                f"  expected: {ref_val!r}\n"
                f"  got:      {parsed_val!r}\n"
                f"  parser output:\n{result.stdout}"
            )
        else:
            passed += 1

    total = len(test_files)
    assert len(errors) == 0, (
        f"{len(errors)}/{total} test files have incorrect output:\n\n"
        + "\n".join(errors[:5])
        + ("\n..." if len(errors) > 5 else "")
    )
    assert passed == total, f"Only {passed}/{total} test files passed"
