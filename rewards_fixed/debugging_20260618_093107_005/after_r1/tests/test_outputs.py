import json
import os


# Expected valid records from the original corrupted sensor.log. Values retain
# both the source-decimal representation and the exact decoded IEEE-754 value.
EXPECTED_RECORDS = [
    (1700000000, 27, 3, 980.51, 980.510009765625),
    (1700003600, 20, 2, 85.91, 85.91000366210938),
    (1700007200, 18, 3, 1001.81, 1001.8099975585938),
    (1700010800, 24, 1, 32.48, 32.47999954223633),
    (1700014400, 17, 3, 1011.36, 1011.3599853515625),
    (1700018000, 40, 3, 989.34, 989.3400268554688),
    (1700021600, 48, 3, 984.56, 984.5599975585938),
    (1700028800, 11, 1, 19.11, 19.110000610351562),
    (1700032400, 5, 1, 31.74, 31.739999771118164),
    (1700036000, 21, 1, 24.18, 24.18000030517578),
    (1700039600, 2, 3, 1028.94, 1028.93994140625),
    (1700043200, 48, 2, 30.21, 30.209999084472656),
    (1700046800, 43, 1, 30.42, 30.420000076293945),
    (1700050400, 12, 2, 35.97, 35.970001220703125),
    (1700057600, 37, 1, 29.73, 29.729999542236328),
    (1700061200, 15, 3, 1009.8, 1009.7999877929688),
    (1700064800, 22, 1, 19.65, 19.649999618530273),
    (1700068400, 32, 1, 29.74, 29.739999771118164),
    (1700072000, 49, 3, 1013.49, 1013.489990234375),
    (1700075600, 17, 3, 1001.68, 1001.6799926757812),
    (1700079200, 9, 1, 34.6, 34.599998474121094),
    (1700086400, 15, 2, 62.35, 62.349998474121094),
    (1700090000, 11, 2, 49.43, 49.43000030517578),
    (1700093600, 4, 1, 29.23, 29.229999542236328),
    (1700097200, 6, 2, 89.68, 89.68000030517578),
    (1700100800, 12, 1, 30.45, 30.450000762939453),
    (1700104400, 24, 1, 28.57, 28.56999969482422),
    (1700108000, 49, 1, 24.62, 24.6200008392334),
    (1700115200, 21, 3, 1028.3, 1028.300048828125),
    (1700118800, 36, 1, 22.29, 22.290000915527344),
    (1700122400, 43, 3, 985.97, 985.969970703125),
    (1700126000, 10, 1, 24.9, 24.899999618530273),
    (1700129600, 22, 1, 19.07, 19.06999969482422),
    (1700133200, 11, 2, 39.01, 39.0099983215332),
    (1700136800, 6, 3, 1014.79, 1014.7899780273438),
]


def load_output():
    assert os.path.exists("/app/recovered.json"), (
        "Expected /app/recovered.json to exist but it was not found"
    )
    with open("/app/recovered.json") as output_file:
        try:
            data = json.load(output_file)
        except json.JSONDecodeError as error:
            assert False, f"recovered.json is not valid JSON: {error}"
    assert isinstance(data, list), f"Expected a JSON array, got {type(data).__name__}"
    return data


def test_recovered_records():
    data = load_output()
    assert len(data) == len(EXPECTED_RECORDS), (
        f"Expected exactly {len(EXPECTED_RECORDS)} valid records, got {len(data)}"
    )

    actual_by_identity = {}
    for index, record in enumerate(data):
        assert "timestamp" in record, f"Record missing 'timestamp': {record}"
        assert "sensor_id" in record, f"Record missing 'sensor_id': {record}"
        assert "reading_type" in record, f"Record missing 'reading_type': {record}"
        assert "value" in record, f"Record missing 'value': {record}"
        assert isinstance(record["timestamp"], int), f"Record {index}: timestamp must be an integer"
        assert isinstance(record["sensor_id"], int), f"Record {index}: sensor_id must be an integer"
        assert isinstance(record["reading_type"], int), f"Record {index}: reading_type must be an integer"
        assert isinstance(record["value"], (int, float)), f"Record {index}: value must be a number"
        identity = (record["timestamp"], record["sensor_id"], record["reading_type"])
        actual_by_identity[identity] = record["value"]

    for timestamp, sensor_id, reading_type, decimal_value, decoded_value in EXPECTED_RECORDS:
        identity = (timestamp, sensor_id, reading_type)
        assert identity in actual_by_identity, f"Missing expected record: {identity}"
        actual_value = actual_by_identity[identity]
        assert actual_value == decimal_value or actual_value == decoded_value, (
            f"Record {identity} has value {actual_value!r}; expected "
            f"{decimal_value!r} or decoded IEEE-754 value {decoded_value!r}"
        )
