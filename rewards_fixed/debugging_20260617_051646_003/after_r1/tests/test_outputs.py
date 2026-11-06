import hashlib
import json
import subprocess


EXPECTED_ENTRIES = [
    {
        "timestamp": 1718123456789012,
        "level": 1,
        "source": "sensor-a",
        "message": "Temperature reading: 23.5 C",
    },
    {
        "timestamp": 1718123457789012,
        "level": 2,
        "source": "sensor-b",
        "message": "Humidity above threshold: 85%",
    },
    {
        "timestamp": 1718123458789012,
        "level": 0,
        "source": "controller",
        "message": "Heartbeat check OK",
    },
    {
        "timestamp": 1718123459789012,
        "level": 3,
        "source": "sensor-a",
        "message": "Connection lost",
    },
    {
        "timestamp": 1718123460789012,
        "level": 1,
        "source": "sensor-c",
        "message": "Pressure: 1013.25 hPa",
    },
]
ORIGINAL_DATA_SHA256 = "69faee59fe28bc6d6dff8d51ecf894c174e394af4a1b1c48fd3102b213dc05de"
FIELDS = ("timestamp", "level", "source", "message")


def _run_logreader():
    build = subprocess.run(
        ["make", "-C", "/app"], capture_output=True, text=True, timeout=30
    )
    assert build.returncode == 0, (
        f"make failed with code {build.returncode}\n"
        f"stdout: {build.stdout}\nstderr: {build.stderr}"
    )

    result = subprocess.run(
        ["/app/logreader", "/app/sensor_data.bin"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"logreader exited with code {result.returncode}\nstderr: {result.stderr}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"Output is not valid JSON: {error}\nOutput: {result.stdout[:500]}"
        ) from error


def _find_entries(value):
    entries = []
    if isinstance(value, dict):
        if set(FIELDS).issubset(value):
            entries.append({field: value[field] for field in FIELDS})
        else:
            for child in value.values():
                entries.extend(_find_entries(child))
    elif isinstance(value, list):
        for child in value:
            entries.extend(_find_entries(child))
    return entries


def test_reader_outputs_valid_json():
    data = _run_logreader()
    assert isinstance(data, (list, dict)), (
        f"Expected a JSON container, got {type(data).__name__}"
    )


def test_all_entries_and_fields_match_binary_log():
    data = _run_logreader()
    entries = _find_entries(data)
    assert len(entries) == len(EXPECTED_ENTRIES), (
        f"Expected {len(EXPECTED_ENTRIES)} entries, got {len(entries)}. "
        "The header entry count is wrong, so the reader must read past it."
    )
    for index, (actual, expected) in enumerate(zip(entries, EXPECTED_ENTRIES)):
        assert actual == expected, (
            f"Entry {index} does not match the bytes in sensor_data.bin: "
            f"expected {expected}, got {actual}"
        )


def test_binary_data_file_is_unchanged():
    with open("/app/sensor_data.bin", "rb") as data_file:
        actual_hash = hashlib.sha256(data_file.read()).hexdigest()
    assert actual_hash == ORIGINAL_DATA_SHA256, "Do not modify the binary data file"
