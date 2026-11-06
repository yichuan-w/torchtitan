import json
import os
import subprocess
import sys


def _run_logreader():
    """Compile and run the logreader, return parsed JSON."""
    # Build
    result = subprocess.run(
        ["make", "-C", "/app"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"make failed with code {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Run
    binary = "/app/logreader"
    result = subprocess.run(
        [binary, "/app/sensor_data.bin"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"logreader exited with code {result.returncode}\n"
        f"stderr: {result.stderr}"
    )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        assert False, f"Output is not valid JSON: {e}\nOutput: {result.stdout[:500]}"

    return data


def test_reader_outputs_valid_json():
    """Test that the compiled program outputs valid JSON."""
    data = _run_logreader()
    assert isinstance(data, list), f"Expected a JSON array, got {type(data).__name__}"


def test_all_five_entries_found():
    """Test that all 5 entries are extracted (not just the 3 from the header)."""
    data = _run_logreader()
    assert len(data) == 5, (
        f"Expected 5 entries, got {len(data)}. "
        f"The header entry count is wrong, so you must read past it."
    )


def test_entry_structure():
    """Test that each entry has the required fields with correct types."""
    data = _run_logreader()

    required_fields = {"timestamp", "level", "source", "message"}
    for i, entry in enumerate(data):
        missing = required_fields - set(entry.keys())
        assert not missing, f"Entry {i} missing fields: {missing}"

        assert isinstance(entry["timestamp"], int), (
            f"Entry {i}: timestamp should be int, got {type(entry['timestamp']).__name__}"
        )
        assert isinstance(entry["level"], int), (
            f"Entry {i}: level should be int, got {type(entry['level']).__name__}"
        )
        assert 0 <= entry["level"] <= 3, (
            f"Entry {i}: level {entry['level']} is out of range [0, 3]"
        )
        assert isinstance(entry["source"], str), (
            f"Entry {i}: source should be str, got {type(entry['source']).__name__}"
        )
        assert isinstance(entry["message"], str), (
            f"Entry {i}: message should be str, got {type(entry['message']).__name__}"
        )


def test_entry_0_correct():
    """Test entry 0: INFO from sensor-a about temperature."""
    data = _run_logreader()
    e = data[0]
    assert e["timestamp"] == 1718123456789012, (
        f"Entry 0 timestamp: expected 1718123456789012, got {e['timestamp']}"
    )
    assert e["level"] == 1, f"Entry 0 level: expected 1 (INFO), got {e['level']}"
    assert e["source"] == "sensor-a", f"Entry 0 source: expected 'sensor-a', got '{e['source']}'"
    assert e["message"] == "Temperature reading: 23.5 C", (
        f"Entry 0 message: expected 'Temperature reading: 23.5 C', got '{e['message']}'"
    )


def test_entry_1_correct():
    """Test entry 1: WARN from sensor-b about humidity."""
    data = _run_logreader()
    e = data[1]
    assert e["timestamp"] == 1718123457789012, (
        f"Entry 1 timestamp: expected 1718123457789012, got {e['timestamp']}"
    )
    assert e["level"] == 2, f"Entry 1 level: expected 2 (WARN), got {e['level']}"
    assert e["source"] == "sensor-b", f"Entry 1 source: expected 'sensor-b', got '{e['source']}'"
    assert e["message"] == "Humidity above threshold: 85%", (
        f"Entry 1 message mismatch"
    )


def test_entry_2_correct():
    """Test entry 2: DEBUG from controller about heartbeat."""
    data = _run_logreader()
    e = data[2]
    assert e["timestamp"] == 1718123458789012, (
        f"Entry 2 timestamp: expected 1718123458789012, got {e['timestamp']}"
    )
    assert e["level"] == 0, f"Entry 2 level: expected 0 (DEBUG), got {e['level']}"
    assert e["source"] == "controller", f"Entry 2 source: expected 'controller', got '{e['source']}'"
    assert e["message"] == "Heartbeat check OK", (
        f"Entry 2 message mismatch"
    )


def test_entry_3_correct():
    """Test entry 3: ERROR from sensor-a about connection lost."""
    data = _run_logreader()
    e = data[3]
    assert e["timestamp"] == 1718123459789012, (
        f"Entry 3 timestamp: expected 1718123459789012, got {e['timestamp']}"
    )
    assert e["level"] == 3, f"Entry 3 level: expected 3 (ERROR), got {e['level']}"
    assert e["source"] == "sensor-a", f"Entry 3 source: expected 'sensor-a', got '{e['source']}'"
    assert e["message"] == "Connection lost", (
        f"Entry 3 message mismatch"
    )


def test_entry_4_correct():
    """Test entry 4: INFO from sensor-c about pressure."""
    data = _run_logreader()
    e = data[4]
    assert e["timestamp"] == 1718123460789012, (
        f"Entry 4 timestamp: expected 1718123460789012, got {e['timestamp']}"
    )
    assert e["level"] == 1, f"Entry 4 level: expected 1 (INFO), got {e['level']}"
    assert e["source"] == "sensor-c", f"Entry 4 source: expected 'sensor-c', got '{e['source']}'"
    assert e["message"] == "Pressure: 1013.25 hPa", (
        f"Entry 4 message mismatch"
    )