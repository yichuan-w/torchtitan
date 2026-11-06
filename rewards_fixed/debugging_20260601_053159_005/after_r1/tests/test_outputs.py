import json
from pathlib import Path


OUTPUT = Path("/app/valid_records.json")
EXPECTED = [
    {"type": 1, "timestamp": 1700000000000000, "key": "sensor.temperature", "value": "23.5 C"},
    {"type": 1, "timestamp": 1700000001000000, "key": "sensor.humidity", "value": "45%"},
    {"type": 3, "timestamp": 1700000003000000, "key": "signal.strength", "value": -42.0},
    {"type": 2, "timestamp": 1700000004000000, "key": "device.boot", "value": "Firmware v3.1.0"},
    {"type": 1, "timestamp": 1700000005000000, "key": "gps.coordinates", "value": "37.7749,-122.4194"},
    {"type": 2, "timestamp": 1700000007000000, "key": "alert.low_battery", "value": "Battery below 20%"},
    {"type": 1, "timestamp": 1700000008000000, "key": "network.status", "value": "connected"},
    {"type": 2, "timestamp": 1700000009000000, "key": "system.check", "value": "All systems nominal"},
]


def load_output():
    assert OUTPUT.is_file(), "missing /app/valid_records.json"
    return json.loads(OUTPUT.read_text(encoding="utf-8"))


def test_all_and_only_valid_records():
    records = load_output()
    projected = [
        {field: record[field] for field in ("type", "timestamp", "key", "value")}
        for record in records
    ]
    assert sorted(projected, key=lambda record: record["timestamp"]) == EXPECTED


def test_record_schema_and_types():
    records = load_output()
    assert all(
        isinstance(record, dict)
        and {"type", "timestamp", "key", "value"} <= record.keys()
        and type(record["type"]) is int
        and type(record["timestamp"]) is int
        and type(record["key"]) is str
        and (
            type(record["value"]) in (int, float)
            if record["type"] == 3
            else type(record["value"]) is str
        )
        for record in records
    )
