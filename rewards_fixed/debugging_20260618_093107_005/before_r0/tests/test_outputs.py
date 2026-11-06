import json
import os


# Expected valid records from the corrupted sensor.log
# Generated with seed 12345, 40 records, 4 corrupted, 1 truncated
EXPECTED_RECORDS = [
    {"timestamp": 1700000000, "sensor_id": 27, "reading_type": 3, "value": 980.51},
    {"timestamp": 1700003600, "sensor_id": 20, "reading_type": 2, "value": 85.91},
    {"timestamp": 1700007200, "sensor_id": 18, "reading_type": 3, "value": 1001.81},
    {"timestamp": 1700010800, "sensor_id": 24, "reading_type": 1, "value": 32.48},
    {"timestamp": 1700014400, "sensor_id": 17, "reading_type": 3, "value": 1011.36},
    {"timestamp": 1700018000, "sensor_id": 40, "reading_type": 3, "value": 989.34},
    {"timestamp": 1700021600, "sensor_id": 48, "reading_type": 3, "value": 984.56},
    {"timestamp": 1700028800, "sensor_id": 11, "reading_type": 1, "value": 19.11},
    {"timestamp": 1700032400, "sensor_id": 5, "reading_type": 1, "value": 31.74},
    {"timestamp": 1700036000, "sensor_id": 21, "reading_type": 1, "value": 24.18},
    {"timestamp": 1700039600, "sensor_id": 2, "reading_type": 3, "value": 1028.94},
    {"timestamp": 1700043200, "sensor_id": 48, "reading_type": 2, "value": 30.21},
    {"timestamp": 1700046800, "sensor_id": 43, "reading_type": 1, "value": 30.42},
    {"timestamp": 1700050400, "sensor_id": 12, "reading_type": 2, "value": 35.97},
    {"timestamp": 1700057600, "sensor_id": 37, "reading_type": 1, "value": 29.73},
    {"timestamp": 1700061200, "sensor_id": 15, "reading_type": 3, "value": 1009.8},
    {"timestamp": 1700064800, "sensor_id": 22, "reading_type": 1, "value": 19.65},
    {"timestamp": 1700068400, "sensor_id": 32, "reading_type": 1, "value": 29.74},
    {"timestamp": 1700072000, "sensor_id": 49, "reading_type": 3, "value": 1013.49},
    {"timestamp": 1700075600, "sensor_id": 17, "reading_type": 3, "value": 1001.68},
    {"timestamp": 1700079200, "sensor_id": 9, "reading_type": 1, "value": 34.6},
    {"timestamp": 1700086400, "sensor_id": 15, "reading_type": 2, "value": 62.35},
    {"timestamp": 1700090000, "sensor_id": 11, "reading_type": 2, "value": 49.43},
    {"timestamp": 1700093600, "sensor_id": 4, "reading_type": 1, "value": 29.23},
    {"timestamp": 1700097200, "sensor_id": 6, "reading_type": 2, "value": 89.68},
    {"timestamp": 1700100800, "sensor_id": 12, "reading_type": 1, "value": 30.45},
    {"timestamp": 1700104400, "sensor_id": 24, "reading_type": 1, "value": 28.57},
    {"timestamp": 1700108000, "sensor_id": 49, "reading_type": 1, "value": 24.62},
    {"timestamp": 1700115200, "sensor_id": 21, "reading_type": 3, "value": 1028.3},
    {"timestamp": 1700118800, "sensor_id": 36, "reading_type": 1, "value": 22.29},
    {"timestamp": 1700122400, "sensor_id": 43, "reading_type": 3, "value": 985.97},
    {"timestamp": 1700126000, "sensor_id": 10, "reading_type": 1, "value": 24.9},
    {"timestamp": 1700129600, "sensor_id": 22, "reading_type": 1, "value": 19.07},
    {"timestamp": 1700133200, "sensor_id": 11, "reading_type": 2, "value": 39.01},
    {"timestamp": 1700136800, "sensor_id": 6, "reading_type": 3, "value": 1014.79},
]


def test_json_file_exists():
    """Verify the recovered.json file exists."""
    assert os.path.exists("/app/recovered.json"), (
        "Expected /app/recovered.json to exist but it was not found"
    )


def test_json_is_valid():
    """Verify the recovered.json contains valid JSON."""
    with open("/app/recovered.json") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            assert False, f"recovered.json is not valid JSON: {e}"
    assert isinstance(data, list), (
        f"Expected a JSON array, got {type(data).__name__}"
    )


def test_record_count():
    """Verify the correct number of records were recovered."""
    with open("/app/recovered.json") as f:
        data = json.load(f)
    assert len(data) >= 35, (
        f"Expected at least 35 valid records, got {len(data)}"
    )
    assert len(data) <= 35, (
        f"Expected at most 35 valid records (corrupted records should be excluded), "
        f"got {len(data)}"
    )


def test_all_expected_records_present():
    """Verify all expected valid records are present."""
    with open("/app/recovered.json") as f:
        data = json.load(f)

    expected_set = {
        (r["timestamp"], r["sensor_id"], r["reading_type"], r["value"])
        for r in EXPECTED_RECORDS
    }
    actual_set = set()
    for r in data:
        assert "timestamp" in r, f"Record missing 'timestamp': {r}"
        assert "sensor_id" in r, f"Record missing 'sensor_id': {r}"
        assert "reading_type" in r, f"Record missing 'reading_type': {r}"
        assert "value" in r, f"Record missing 'value': {r}"
        actual_set.add((r["timestamp"], r["sensor_id"], r["reading_type"], r["value"]))

    missing = expected_set - actual_set
    assert len(missing) == 0, (
        f"Missing {len(missing)} expected records: {sorted(missing)[:5]}..."
    )


def test_field_types():
    """Verify all records have correct field types."""
    with open("/app/recovered.json") as f:
        data = json.load(f)

    for i, r in enumerate(data):
        assert isinstance(r["timestamp"], int), (
            f"Record {i}: 'timestamp' should be an integer, got {type(r['timestamp']).__name__}"
        )
        assert isinstance(r["sensor_id"], int), (
            f"Record {i}: 'sensor_id' should be an integer, got {type(r['sensor_id']).__name__}"
        )
        assert isinstance(r["reading_type"], int), (
            f"Record {i}: 'reading_type' should be an integer, "
            f"got {type(r['reading_type']).__name__} = {r['reading_type']!r}"
        )
        assert isinstance(r["value"], (int, float)), (
            f"Record {i}: 'value' should be a number, got {type(r['value']).__name__}"
        )


def test_no_corrupted_records():
    """Verify corrupted records are not included."""
    # Corrupted record timestamps (indices 7, 15, 23, 31)
    corrupted_timestamps = {1700025200, 1700054000, 1700082800, 1700111600}
    # Also, the truncated last record timestamp
    truncated_timestamp = 1700140400

    with open("/app/recovered.json") as f:
        data = json.load(f)

    for r in data:
        assert r["timestamp"] not in corrupted_timestamps, (
            f"Corrupted record with timestamp {r['timestamp']} was included. "
            f"This record should have been excluded."
        )
        assert r["timestamp"] != truncated_timestamp, (
            f"Truncated record with timestamp {r['timestamp']} was included. "
            f"This record should have been excluded."
        )