# Tests for sensor data aggregation task
import os
import csv


def test_output_file_exists():
    """Test that the output file exists."""
    assert os.path.exists("/app/output/hourly_aggregates.csv"), \
        "Output file /app/output/hourly_aggregates.csv does not exist"


def test_output_has_correct_columns():
    """Test that the output file has the correct columns."""
    with open("/app/output/hourly_aggregates.csv", "r") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        
    expected_columns = ["sensor_id", "hour_start", "mean_temp", "min_temp", "max_temp"]
    assert columns == expected_columns, \
        f"Expected columns {expected_columns}, got {columns}"


def test_output_has_correct_sensors():
    """Test that all sensors are present in the output."""
    with open("/app/output/hourly_aggregates.csv", "r") as f:
        reader = csv.DictReader(f)
        sensors = set(row["sensor_id"] for row in reader)
    
    expected_sensors = {"sensor_01", "sensor_02", "sensor_03"}
    assert sensors == expected_sensors, \
        f"Expected sensors {expected_sensors}, got {sensors}"


def test_hour_start_format():
    """Test that hour_start is in UTC format."""
    with open("/app/output/hourly_aggregates.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            hour_start = row["hour_start"]
            # Should end with +00:00 for UTC
            assert hour_start.endswith("+00:00"), \
                f"hour_start should be in UTC format (end with +00:00), got: {hour_start}"
            # Should be in YYYY-MM-DD HH:MM:SS format before the timezone
            datetime_part = hour_start.replace("+00:00", "")
            assert len(datetime_part) == 19, \
                f"hour_start datetime part should be 19 chars (YYYY-MM-DD HH:MM:SS), got: {datetime_part}"
            break  # Just check first row


def test_aggregate_values_sample():
    """Test that a sample of aggregated values are correct."""
    # Expected values for specific hours (pre-computed)
    expected_samples = {
        ("sensor_01", "2024-04-24 22:00:00+00:00"): {"mean": 28.600, "min": 28.60, "max": 28.60},
        ("sensor_01", "2024-04-25 00:00:00+00:00"): {"mean": 28.465, "min": 27.49, "max": 29.44},
        ("sensor_02", "2024-04-25 12:00:00+00:00"): {"mean": 24.710, "min": 19.26, "max": 30.16},
        ("sensor_03", "2024-04-26 22:00:00+00:00"): {"mean": 17.445, "min": 16.13, "max": 18.76},
        ("sensor_01", "2024-04-27 20:00:00+00:00"): {"mean": 18.085, "min": 17.89, "max": 18.28},
    }
    
    with open("/app/output/hourly_aggregates.csv", "r") as f:
        reader = csv.DictReader(f)
        rows = {(row["sensor_id"], row["hour_start"]): row for row in reader}
    
    for key, exp in expected_samples.items():
        assert key in rows, f"Missing expected row: {key}"
        row = rows[key]
        
        mean_val = float(row["mean_temp"])
        min_val = float(row["min_temp"])
        max_val = float(row["max_temp"])
        
        # Check values with tolerance for floating point
        assert abs(mean_val - exp["mean"]) < 0.1, \
            f"Mean mismatch for {key}: expected {exp['mean']}, got {mean_val}"
        assert abs(min_val - exp["min"]) < 0.1, \
            f"Min mismatch for {key}: expected {exp['min']}, got {min_val}"
        assert abs(max_val - exp["max"]) < 0.1, \
            f"Max mismatch for {key}: expected {exp['max']}, got {max_val}"


def test_dst_transition_handling():
    """Test that DST transition on April 26 is handled correctly.
    
    On April 26, 2024 in Cairo, clocks jumped forward at midnight,
    so 00:00-00:59 Cairo time doesn't exist. The solver must handle this.
    
    After DST:
    - 00:00 and 00:30 Cairo (nonexistent) get shifted to 01:00 Cairo
    - 01:00 Cairo = 22:00 UTC on April 25
    """
    with open("/app/output/hourly_aggregates.csv", "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    hour_starts = {(row["sensor_id"], row["hour_start"]) for row in rows}
    
    # We should have data for April 25 22:00 UTC
    # This hour contains both shifted nonexistent times (00:00/00:30 Cairo) 
    # and actual 01:00 Cairo readings
    assert ("sensor_01", "2024-04-25 22:00:00+00:00") in hour_starts, \
        "Missing data for April 25 22:00 UTC (contains DST-shifted readings)"
    
    # We should have data for April 25 23:00 UTC (02:00 Cairo after DST)
    assert ("sensor_01", "2024-04-25 23:00:00+00:00") in hour_starts, \
        "Missing data for April 25 23:00 UTC (02:00 Cairo after DST)"


def test_no_duplicate_readings_in_output():
    """Test that duplicates were properly removed."""
    with open("/app/output/hourly_aggregates.csv", "r") as f:
        reader = csv.DictReader(f)
        keys = [(row["sensor_id"], row["hour_start"]) for row in reader]
    
    # Check for duplicates
    assert len(keys) == len(set(keys)), \
        f"Found duplicate (sensor_id, hour_start) pairs in output"
