"""Tests for sensor data binary format recovery task.
Verifies valid records were extracted from corrupted sensor data.
"""
import json
import os

EXPECTED_RECORDS = [
    {"type": 1, "timestamp": 1700000000000000, "key": "sensor.temperature",
     "value": "23.5 C"},
    {"type": 1, "timestamp": 1700000001000000, "key": "sensor.humidity",
     "value": "45%"},
    # battery.voltage has corrupted value-length field — excluded
    {"type": 3, "timestamp": 1700000003000000, "key": "signal.strength",
     "value": -42.0},
    {"type": 2, "timestamp": 1700000004000000, "key": "device.boot",
     "value": "Firmware v3.1.0"},
    {"type": 1, "timestamp": 1700000005000000, "key": "gps.coordinates",
     "value": "37.7749,-122.4194"},
    # memory.free has corrupted CRC — excluded
    {"type": 2, "timestamp": 1700000007000000, "key": "alert.low_battery",
     "value": "Battery below 20%"},
    {"type": 1, "timestamp": 1700000008000000, "key": "network.status",
     "value": "connected"},
    {"type": 2, "timestamp": 1700000009000000, "key": "system.check",
     "value": "All systems nominal"},
]


def test_recovered_file_exists():
    assert os.path.exists('/app/valid_records.json')


def test_valid_json_array():
    with open('/app/valid_records.json') as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) > 0


def test_record_count():
    with open('/app/valid_records.json') as f:
        data = json.load(f)
    assert len(data) == len(EXPECTED_RECORDS), (
        f"Expected {len(EXPECTED_RECORDS)} valid records, got {len(data)}"
    )


def test_all_records_present_and_correct():
    with open('/app/valid_records.json') as f:
        data = json.load(f)

    got_by_key = {item['key']: item for item in data}

    for expected in EXPECTED_RECORDS:
        key = expected['key']
        assert key in got_by_key, f"Missing record: {key}"
        got = got_by_key[key]
        assert got['type'] == expected['type'], f"Type mismatch for '{key}'"
        assert got['timestamp'] == expected['timestamp'], f"TS mismatch for '{key}'"
        assert got['value'] == expected['value'], f"Value mismatch for '{key}'"

    expected_keys = {r['key'] for r in EXPECTED_RECORDS}
    got_keys = set(got_by_key.keys())
    assert got_keys == expected_keys, (
        f"Extra: {got_keys - expected_keys}, Missing: {expected_keys - got_keys}"
    )
