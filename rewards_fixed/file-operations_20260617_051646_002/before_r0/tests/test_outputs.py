import json
import subprocess
import base64
import datetime
import os
from pathlib import Path


def bplist_to_json_compatible(obj):
    """Convert plistlib-parsed objects to JSON-compatible structures."""
    import plistlib

    if obj is None:
        return None
    elif isinstance(obj, bool):
        return obj
    elif isinstance(obj, int):
        return obj
    elif isinstance(obj, float):
        return obj
    elif isinstance(obj, str):
        return obj
    elif isinstance(obj, bytes):
        return base64.b64encode(obj).decode('ascii')
    elif isinstance(obj, datetime.datetime):
        return obj.strftime('%Y-%m-%dT%H:%M:%SZ')
    elif isinstance(obj, plistlib.UID):
        return {"__uid__": int(obj)}
    elif isinstance(obj, list):
        return [bplist_to_json_compatible(item) for item in obj]
    elif isinstance(obj, dict):
        return {str(k): bplist_to_json_compatible(v) for k, v in obj.items()}
    else:
        return str(obj)


def normalize_value(value):
    """Normalize a JSON value for comparison: convert ints to float if they look like floats."""
    if isinstance(value, dict) and "__uid__" in value:
        return {"__uid__": int(value["__uid__"])}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # If the float is a whole number, treat as int for comparison
        if isinstance(value, float) and value == int(value) and abs(value) <= 2**53:
            return int(value)
        return value
    if isinstance(value, str):
        # Try to convert numeric strings to numbers for comparison
        try:
            if '.' in value or 'e' in value.lower():
                return float(value)
            return int(value)
        except (ValueError, OverflowError):
            return value
    if value is None:
        return None
    return value


def deep_compare(a, b, path=""):
    """Compare two JSON-compatible structures, returning differences."""
    a = normalize_value(a)
    b = normalize_value(b)

    if type(a) != type(b):
        return [f"Type mismatch at {path}: {type(a).__name__} vs {type(b).__name__}"]

    if isinstance(a, dict):
        diffs = []
        all_keys = set(a.keys()) | set(b.keys())
        for k in sorted(all_keys):
            if k not in a:
                diffs.append(f"Missing key '{k}' in solver output at {path}")
            elif k not in b:
                diffs.append(f"Extra key '{k}' in solver output at {path}")
            else:
                diffs.extend(deep_compare(a[k], b[k], f"{path}.{k}"))
        return diffs

    elif isinstance(a, list):
        if len(a) != len(b):
            return [f"List length mismatch at {path}: {len(a)} vs {len(b)}"]
        diffs = []
        for i in range(len(a)):
            diffs.extend(deep_compare(a[i], b[i], f"{path}[{i}]"))
        return diffs

    elif isinstance(a, float) and isinstance(b, float):
        if abs(a - b) > 1e-6:
            return [f"Float mismatch at {path}: {a} vs {b}"]
        return []

    else:
        if a != b:
            return [f"Value mismatch at {path}: {a!r} vs {b!r}"]
        return []


def generate_test_plist():
    """Generate a complex bplist file for testing."""
    import plistlib

    data = {
        "null": None,
        "yes": True,
        "no": False,
        "small_int": 42,
        "medium_int": 1000,
        "large_int": 2**40,
        "negative": -12345,
        "float_val": 3.14159,
        "ascii_str": "Hello, World!",
        "unicode_str": "你好世界",
        "data_blob": b"\x00\x01\x02\x03\xFF\xFE\xFD",
        "date_val": datetime.datetime(2024, 6, 15, 12, 30, 0),
        "empty_arr": [],
        "simple_arr": [1, "two", 3.0],
        "empty_dict": {},
        "nested": {
            "a": 1,
            "b": [2, 3, 4],
            "c": {"deep": "value"},
        },
        "uid_val": plistlib.UID(99999),
    }

    plist_path = "/tmp/test_generated.plist"
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)

    # Parse to get expected output
    with open(plist_path, "rb") as f:
        parsed = plistlib.load(f)

    expected = bplist_to_json_compatible(parsed)
    return plist_path, expected


def run_solver(plist_path):
    """Run the solver's parse.js on the given plist file."""
    result = subprocess.run(
        ["node", "/app/parse.js", plist_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


def test_parse_js_exists():
    """Test that parse.js exists and is a file."""
    parse_path = Path("/app/parse.js")
    assert parse_path.exists(), f"parse.js does not exist at {parse_path}"
    assert parse_path.is_file(), f"{parse_path} is not a file"


def test_parse_js_runs():
    """Test that parse.js runs without error on the sample file."""
    result = run_solver("/app/input.plist")
    assert result.returncode == 0, (
        f"parse.js exited with code {result.returncode}\n"
        f"stderr: {result.stderr}"
    )


def test_parse_js_outputs_valid_json():
    """Test that parse.js outputs valid JSON."""
    result = run_solver("/app/input.plist")
    assert result.returncode == 0, (
        f"parse.js exited with code {result.returncode}\n"
        f"stderr: {result.stderr}"
    )

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        assert False, f"Output is not valid JSON: {e}\nOutput: {result.stdout[:500]}"

    assert isinstance(parsed, dict), (
        f"Expected JSON object at top level, got {type(parsed).__name__}"
    )


def test_parse_generated_file_matches_reference():
    """Test that parse.js output matches the reference implementation on a generated file."""
    import plistlib

    plist_path, expected = generate_test_plist()

    result = run_solver(plist_path)
    assert result.returncode == 0, (
        f"parse.js failed on generated file:\nstderr: {result.stderr}"
    )

    try:
        solver_output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        assert False, f"Output is not valid JSON: {e}\nOutput: {result.stdout[:500]}"

    # Compare
    diffs = deep_compare(expected, solver_output)

    if diffs:
        diff_msg = "\n".join(diffs[:20])
        assert False, (
            f"Found {len(diffs)} differences between solver output and reference:\n"
            f"{diff_msg}\n\n"
            f"Expected: {json.dumps(expected, indent=2)[:1000]}\n\n"
            f"Solver output: {json.dumps(solver_output, indent=2)[:1000]}"
        )


def test_parse_simple_types():
    """Test that parse.js correctly handles a plist with only simple types."""
    import plistlib

    data = {
        "a": None,
        "b": True,
        "c": False,
        "d": 42,
        "e": -100,
        "f": 3.14,
        "g": "hello",
    }

    plist_path = "/tmp/test_simple.plist"
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)

    with open(plist_path, "rb") as f:
        parsed = plistlib.load(f)
    expected = bplist_to_json_compatible(parsed)

    result = run_solver(plist_path)
    assert result.returncode == 0, f"parse.js failed: {result.stderr}"

    try:
        solver_output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        assert False, f"Invalid JSON: {e}"

    diffs = deep_compare(expected, solver_output)
    if diffs:
        assert False, f"Simple types test failed:\n" + "\n".join(diffs[:10])


def test_parse_nested_structures():
    """Test that parse.js correctly handles deeply nested arrays and dicts."""
    import plistlib

    data = {
        "level1": {
            "level2": {
                "level3": [1, [2, [3, [4, 5]]], {"a": [{"b": "deep"}]}],
            }
        }
    }

    plist_path = "/tmp/test_nested.plist"
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)

    with open(plist_path, "rb") as f:
        parsed = plistlib.load(f)
    expected = bplist_to_json_compatible(parsed)

    result = run_solver(plist_path)
    assert result.returncode == 0, f"parse.js failed: {result.stderr}"

    try:
        solver_output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        assert False, f"Invalid JSON: {e}"

    diffs = deep_compare(expected, solver_output)
    if diffs:
        assert False, f"Nested structures test failed:\n" + "\n".join(diffs[:10])


def test_parse_data_and_date():
    """Test that parse.js correctly handles data (bytes) and date types."""
    import plistlib

    data = {
        "raw": b"\x00\x01\x02\xFF\xFE\xFD",
        "when": datetime.datetime(2023, 1, 1, 0, 0, 0),
        "empty_data": b"",
    }

    plist_path = "/tmp/test_data_date.plist"
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)

    with open(plist_path, "rb") as f:
        parsed = plistlib.load(f)
    expected = bplist_to_json_compatible(parsed)

    result = run_solver(plist_path)
    assert result.returncode == 0, f"parse.js failed: {result.stderr}"

    try:
        solver_output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        assert False, f"Invalid JSON: {e}"

    diffs = deep_compare(expected, solver_output)
    if diffs:
        assert False, f"Data/Date test failed:\n" + "\n".join(diffs[:10])


def test_parse_uid():
    """Test that parse.js correctly handles UID values."""
    import plistlib

    data = {
        "uid_small": plistlib.UID(1),
        "uid_large": plistlib.UID(2**31),
    }

    plist_path = "/tmp/test_uid.plist"
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)

    with open(plist_path, "rb") as f:
        parsed = plistlib.load(f)
    expected = bplist_to_json_compatible(parsed)

    result = run_solver(plist_path)
    assert result.returncode == 0, f"parse.js failed: {result.stderr}"

    try:
        solver_output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        assert False, f"Invalid JSON: {e}"

    diffs = deep_compare(expected, solver_output)
    if diffs:
        assert False, f"UID test failed:\n" + "\n".join(diffs[:10])


def test_parse_large_integers():
    """Test that parse.js correctly handles large integers (near 2^63)."""
    import plistlib

    data = {
        "big_pos": 2**63 - 1,
        "big_neg": -(2**63),
        "power_of_two": 2**48,
    }

    plist_path = "/tmp/test_largeints.plist"
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)

    with open(plist_path, "rb") as f:
        parsed = plistlib.load(f)
    expected = bplist_to_json_compatible(parsed)

    result = run_solver(plist_path)
    assert result.returncode == 0, f"parse.js failed: {result.stderr}"

    try:
        solver_output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        assert False, f"Invalid JSON: {e}"

    diffs = deep_compare(expected, solver_output)
    if diffs:
        assert False, f"Large integers test failed:\n" + "\n".join(diffs[:10])