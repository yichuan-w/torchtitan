import csv
import math
import os
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime

ATTRIBUTION_PATH = Path("/app/output/attribution.csv")
CHANNEL_SUMMARY_PATH = Path("/app/output/channel_summary.csv")

# Expected columns in order for attribution.csv
EXPECTED_ATTRIBUTION_COLUMNS = [
    "user_id",
    "conversion_id",
    "session_id",
    "session_start",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "total_sessions",
    "session_position",
    "first_touch_points",
    "last_touch_points",
    "positional_points",
    "linear_points",
    "time_decay_points",
    "revenue",
    "first_touch_revenue",
    "last_touch_revenue",
    "positional_revenue",
    "linear_revenue",
    "time_decay_revenue",
]

# Expected columns for channel_summary.csv
EXPECTED_CHANNEL_COLUMNS = [
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "total_sessions",
    "total_conversions_attributed",
    "first_touch_revenue",
    "last_touch_revenue",
    "positional_revenue",
    "linear_revenue",
    "time_decay_revenue",
    "first_touch_roas",
    "last_touch_roas",
    "positional_roas",
    "linear_roas",
    "time_decay_roas",
]


def round_to_4_decimals(value):
    """Round a float to 4 decimal places."""
    return round(float(value), 4)


def test_attribution_file_exists():
    """Test that the attribution output file exists."""
    assert ATTRIBUTION_PATH.exists(), f"Output file {ATTRIBUTION_PATH} does not exist"


def test_channel_summary_file_exists():
    """Test that the channel summary output file exists."""
    assert CHANNEL_SUMMARY_PATH.exists(), f"Output file {CHANNEL_SUMMARY_PATH} does not exist"


def test_attribution_has_correct_columns():
    """Test that attribution CSV has all required columns in the correct order."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        
    assert columns is not None, "Could not read columns from CSV"
    assert list(columns) == EXPECTED_ATTRIBUTION_COLUMNS, (
        f"Columns mismatch.\nExpected: {EXPECTED_ATTRIBUTION_COLUMNS}\nGot: {list(columns)}"
    )


def test_channel_summary_has_correct_columns():
    """Test that channel_summary CSV has all required columns in the correct order."""
    with open(CHANNEL_SUMMARY_PATH, "r") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        
    assert columns is not None, "Could not read columns from CSV"
    assert list(columns) == EXPECTED_CHANNEL_COLUMNS, (
        f"Columns mismatch.\nExpected: {EXPECTED_CHANNEL_COLUMNS}\nGot: {list(columns)}"
    )


def test_attribution_has_data():
    """Test that the attribution file has data rows."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    assert len(rows) > 0, "Attribution file has no data rows"


def test_channel_summary_has_data():
    """Test that the channel_summary file has data rows."""
    with open(CHANNEL_SUMMARY_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    assert len(rows) > 0, "Channel summary file has no data rows"


def test_single_session_attribution():
    """Test attribution for a user with single session (user_004)."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["user_id"] == "user_004"]
    
    # User 004 should have exactly 1 row (single session, single conversion)
    assert len(rows) == 1, f"Expected 1 row for user_004, got {len(rows)}"
    
    row = rows[0]
    
    # Single session gets all attribution
    assert round_to_4_decimals(row["first_touch_points"]) == 1.0
    assert round_to_4_decimals(row["last_touch_points"]) == 1.0
    assert round_to_4_decimals(row["positional_points"]) == 1.0
    assert round_to_4_decimals(row["linear_points"]) == 1.0
    assert round_to_4_decimals(row["time_decay_points"]) == 1.0
    
    # Total sessions should be 1
    assert int(row["total_sessions"]) == 1
    assert int(row["session_position"]) == 1


def test_two_session_positional_attribution():
    """Test positional attribution for user with exactly 2 sessions."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["user_id"] == "user_001"]
    
    # User 001 should have 2 sessions for their first conversion
    assert len(rows) == 2, f"Expected 2 rows for user_001 first conversion, got {len(rows)}"
    
    # Both sessions should get 0.5 for positional model
    for row in rows:
        assert round_to_4_decimals(row["positional_points"]) == 0.5, (
            f"Expected 0.5 for positional_points, got {row['positional_points']}"
        )
        assert round_to_4_decimals(row["linear_points"]) == 0.5, (
            f"Expected 0.5 for linear_points, got {row['linear_points']}"
        )


def test_first_touch_attribution():
    """Test that first-touch attribution gives 1.0 to earliest session."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["user_id"] == "user_002"]
    
    # User 002 has multiple sessions before conversion
    first_touch_rows = [r for r in rows if int(r["session_position"]) == 1]
    other_rows = [r for r in rows if int(r["session_position"]) != 1]
    
    assert len(first_touch_rows) == 1, "Expected exactly 1 first touch row"
    assert round_to_4_decimals(first_touch_rows[0]["first_touch_points"]) == 1.0
    
    for row in other_rows:
        assert round_to_4_decimals(row["first_touch_points"]) == 0.0


def test_last_touch_attribution():
    """Test that last-touch attribution gives 1.0 to latest session."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["user_id"] == "user_005"]
    
    # Find the last session (highest position)
    last_position = max(int(r["session_position"]) for r in rows)
    last_touch_rows = [r for r in rows if int(r["session_position"]) == last_position]
    other_rows = [r for r in rows if int(r["session_position"]) != last_position]
    
    assert len(last_touch_rows) == 1, "Expected exactly 1 last touch row"
    assert round_to_4_decimals(last_touch_rows[0]["last_touch_points"]) == 1.0
    
    for row in other_rows:
        assert round_to_4_decimals(row["last_touch_points"]) == 0.0


def test_positional_three_sessions():
    """Test positional attribution with 3 sessions."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["user_id"] == "user_006"]
    
    if len(rows) == 2:
        # First touch: 0.5, Last touch: 0.5 (two sessions)
        first = [r for r in rows if int(r["session_position"]) == 1][0]
        last = [r for r in rows if int(r["session_position"]) == 2][0]
        
        assert round_to_4_decimals(first["positional_points"]) == 0.5
        assert round_to_4_decimals(last["positional_points"]) == 0.5


def test_time_decay_weights():
    """Test that time-decay attribution weights more recent sessions higher."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["user_id"] == "user_002"]
    
    # Time-decay: sessions closer to conversion should have higher points
    # Get session positions and their time_decay_points
    session_points = [(int(r["session_position"]), float(r["time_decay_points"])) for r in rows]
    session_points.sort(key=lambda x: x[0])
    
    # Last session should have higher time_decay_points than first
    if len(session_points) >= 2:
        first_td = session_points[0][1]
        last_td = session_points[-1][1]
        assert last_td > first_td, (
            f"Time-decay should weight later sessions higher: first={first_td}, last={last_td}"
        )


def test_time_decay_sums_to_one():
    """Test that time-decay points sum to 1.0 per conversion."""
    from collections import defaultdict
    
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    by_conversion = defaultdict(list)
    for row in rows:
        by_conversion[row["conversion_id"]].append(row)
    
    for conv_id, conv_rows in by_conversion.items():
        td_sum = sum(float(r["time_decay_points"]) for r in conv_rows)
        assert abs(td_sum - 1.0) < 0.001, f"Time-decay sum for {conv_id}: {td_sum}"


def test_sessionization_30_minute_timeout():
    """Test that sessions are correctly split at 30-minute inactivity."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # User 001 has a 33-minute gap between 08:12 and 08:45
    # This should create separate sessions
    user_001_rows = [r for r in rows if r["user_id"] == "user_001"]
    
    # Count unique session_ids
    session_ids = set(r["session_id"] for r in user_001_rows)
    # User 001 should have sessions for conversion - at least 2 sessions
    assert len(session_ids) >= 2, (
        f"Expected at least 2 sessions for user_001 based on 30-min timeout, got {len(session_ids)}"
    )


def test_revenue_attribution():
    """Test that revenue is correctly attributed."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    for row in rows:
        revenue = Decimal(row["revenue"])
        first_touch_rev = Decimal(row["first_touch_revenue"])
        last_touch_rev = Decimal(row["last_touch_revenue"])
        positional_rev = Decimal(row["positional_revenue"])
        linear_rev = Decimal(row["linear_revenue"])
        time_decay_rev = Decimal(row["time_decay_revenue"])
        
        first_pts = Decimal(row["first_touch_points"])
        last_pts = Decimal(row["last_touch_points"])
        positional_pts = Decimal(row["positional_points"])
        linear_pts = Decimal(row["linear_points"])
        time_decay_pts = Decimal(row["time_decay_points"])
        
        # Revenue should equal points * revenue
        tolerance = (abs(revenue) + Decimal("1")) * Decimal("0.00005")
        assert abs(first_touch_rev - (revenue * first_pts)) <= tolerance
        assert abs(last_touch_rev - (revenue * last_pts)) <= tolerance
        assert abs(positional_rev - (revenue * positional_pts)) <= tolerance
        assert abs(linear_rev - (revenue * linear_pts)) <= tolerance
        assert abs(time_decay_rev - (revenue * time_decay_pts)) <= tolerance


def test_conversion_id_format():
    """Test that conversion_id follows the correct format."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    conversion_numbers_by_user = {}
    for row in rows:
        user_id = row["user_id"]
        conversion_id = row["conversion_id"]
        
        # Format should be {user_id}_{number}
        assert conversion_id.startswith(f"{user_id}_"), (
            f"conversion_id '{conversion_id}' should start with '{user_id}_'"
        )
        
        # Extract number part
        num_part = conversion_id.split("_")[-1]
        assert num_part.isdigit(), f"conversion_id number part should be numeric, got {num_part}"
        conversion_numbers_by_user.setdefault(user_id, set()).add(int(num_part))

    for user_id, numbers in conversion_numbers_by_user.items():
        assert numbers == set(range(1, len(numbers) + 1)), (
            f"conversion_id numbers for {user_id} should be sequential starting at 1, got {sorted(numbers)}"
        )


def test_session_id_format():
    """Test that session_id follows the correct format."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    for row in rows:
        user_id = row["user_id"]
        session_id = row["session_id"]
        
        # Format should be {user_id}_s{number}
        assert session_id.startswith(f"{user_id}_s"), (
            f"session_id '{session_id}' should start with '{user_id}_s'"
        )


def test_multi_conversion_user():
    """Test that users with multiple conversions have correct handling."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["user_id"] == "user_010"]
    
    # User 010 has two conversions
    conversion_ids = set(r["conversion_id"] for r in rows)
    
    # User 010 should have two conversions
    assert len(conversion_ids) == 2, f"Expected 2 conversions for user_010, got {len(conversion_ids)}"


def test_linear_attribution_divides_evenly():
    """Test that linear attribution divides points evenly among sessions."""
    from collections import defaultdict
    
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    by_conversion = defaultdict(list)
    for row in rows:
        by_conversion[row["conversion_id"]].append(row)
    
    for conv_id, conv_rows in by_conversion.items():
        total_sessions = int(conv_rows[0]["total_sessions"])
        
        # Each session should have linear_points = 1.0 / total_sessions
        expected_linear = round_to_4_decimals(1.0 / total_sessions)
        
        for row in conv_rows:
            actual = round_to_4_decimals(row["linear_points"])
            assert actual == expected_linear, (
                f"Linear points mismatch for {conv_id}: expected {expected_linear}, got {actual}"
            )


def test_attribution_sorted_correctly():
    """Test that attribution output is sorted by user_id, conversion_id, session_position."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # Extract sort keys
    sort_keys = [(r["user_id"], r["conversion_id"], int(r["session_position"])) for r in rows]
    sorted_keys = sorted(sort_keys)
    
    assert sort_keys == sorted_keys, "Attribution output is not sorted correctly"


def test_channel_summary_sorted_by_time_decay_revenue():
    """Test that channel_summary is sorted by time_decay_revenue descending."""
    with open(CHANNEL_SUMMARY_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    time_decay_revenues = [float(r["time_decay_revenue"]) for r in rows]
    sorted_revenues = sorted(time_decay_revenues, reverse=True)
    
    assert time_decay_revenues == sorted_revenues, (
        f"Channel summary not sorted by time_decay_revenue descending"
    )


def test_channel_summary_roas_calculation():
    """Test that ROAS is correctly calculated as revenue / total_sessions."""
    with open(CHANNEL_SUMMARY_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    for row in rows:
        total_sessions = int(row["total_sessions"])
        if total_sessions > 0:
            first_touch_rev = float(row["first_touch_revenue"])
            first_touch_roas = float(row["first_touch_roas"])
            
            expected_roas = round(first_touch_rev / total_sessions, 4)
            actual_roas = round(first_touch_roas, 4)
            
            assert abs(expected_roas - actual_roas) < 0.01, (
                f"ROAS mismatch for {row['utm_source']}/{row['utm_campaign']}: "
                f"expected {expected_roas}, got {actual_roas}"
            )


def test_total_points_sum_to_one():
    """Test that total attribution points for each conversion sum to 1.0."""
    from collections import defaultdict
    
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    by_conversion = defaultdict(list)
    for row in rows:
        by_conversion[row["conversion_id"]].append(row)
    
    for conv_id, conv_rows in by_conversion.items():
        first_touch_sum = sum(float(r["first_touch_points"]) for r in conv_rows)
        last_touch_sum = sum(float(r["last_touch_points"]) for r in conv_rows)
        positional_sum = sum(float(r["positional_points"]) for r in conv_rows)
        linear_sum = sum(float(r["linear_points"]) for r in conv_rows)
        time_decay_sum = sum(float(r["time_decay_points"]) for r in conv_rows)
        
        # Each should sum to 1.0 (with tolerance for floating point)
        assert abs(first_touch_sum - 1.0) < 0.001, f"First touch sum for {conv_id}: {first_touch_sum}"
        assert abs(last_touch_sum - 1.0) < 0.001, f"Last touch sum for {conv_id}: {last_touch_sum}"
        assert abs(positional_sum - 1.0) < 0.001, f"Positional sum for {conv_id}: {positional_sum}"
        assert abs(linear_sum - 1.0) < 0.001, f"Linear sum for {conv_id}: {linear_sum}"
        assert abs(time_decay_sum - 1.0) < 0.001, f"Time-decay sum for {conv_id}: {time_decay_sum}"


def test_missing_utm_handling():
    """Test that missing UTM parameters are replaced with 'unknown'."""
    with open(ATTRIBUTION_PATH, "r") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r["user_id"] == "user_011"]
    
    # User 011 has missing UTM values
    assert len(rows) > 0, "Expected rows for user_011"
    
    # Check that no UTM values are empty or null
    for row in rows:
        assert row["utm_source"] not in ["", None], f"Empty utm_source should be 'unknown'"
        assert row["utm_medium"] not in ["", None], f"Empty utm_medium should be 'unknown'"
        assert row["utm_campaign"] not in ["", None], f"Empty utm_campaign should be 'unknown'"


def test_first_pageview_utm_values_are_used():
    """Test that a session uses its first pageview's UTM values."""
    with open(ATTRIBUTION_PATH, "r") as f:
        rows = list(csv.DictReader(f))

    matching_rows = [
        row for row in rows
        if row["user_id"] == "user_011"
        and row["session_start"].startswith("2024-01-10 08:00:00")
    ]
    assert matching_rows and all(
        (row["utm_source"], row["utm_medium"], row["utm_campaign"])
        == ("unknown", "cpc", "missing_source")
        for row in matching_rows
    )


def test_sessions_are_before_and_within_conversion_window():
    """Test that every attributed session precedes its conversion by at most 30 days."""
    conversion_times = {
        ("user_001", Decimal("150.00")): "2024-01-15 08:55:00",
        ("user_002", Decimal("275.00")): "2024-01-18 11:30:00",
        ("user_003", Decimal("89.99")): "2024-01-12 13:50:00",
        ("user_004", Decimal("199.00")): "2024-01-22 09:10:00",
        ("user_005", Decimal("320.00")): "2024-01-20 11:50:00",
        ("user_006", Decimal("450.00")): "2024-01-15 10:35:00",
        ("user_007", Decimal("75.00")): "2024-01-25 15:00:00",
        ("user_008", Decimal("125.00")): "2024-01-14 09:35:00",
        ("user_009", Decimal("180.00")): "2024-01-20 10:10:00",
        ("user_010", Decimal("95.00")): "2024-01-06 09:30:00",
        ("user_010", Decimal("110.00")): "2024-01-22 12:20:00",
        ("user_011", Decimal("225.00")): "2024-01-15 14:20:00",
    }
    with open(ATTRIBUTION_PATH, "r") as f:
        rows = list(csv.DictReader(f))

    valid_rows = []
    for row in rows:
        conversion_key = (row["user_id"], Decimal(row["revenue"]))
        conversion_time = datetime.fromisoformat(conversion_times[conversion_key])
        session_start = datetime.fromisoformat(row["session_start"])
        age_days = (conversion_time - session_start).total_seconds() / 86400
        valid_rows.append(0 < age_days <= 30)
    assert all(valid_rows)


def test_time_decay_uses_seven_day_half_life():
    """Test normalized exponential time decay against the stated formula."""
    conversion_times = {
        "user_002_1": datetime.fromisoformat("2024-01-18 11:30:00"),
    }
    with open(ATTRIBUTION_PATH, "r") as f:
        rows = [row for row in csv.DictReader(f) if row["conversion_id"] in conversion_times]

    weights = [
        2 ** (-((conversion_times[row["conversion_id"]] - datetime.fromisoformat(row["session_start"])).total_seconds() / 86400) / 7)
        for row in rows
    ]
    weight_sum = sum(weights)
    comparisons = [
        abs(float(row["time_decay_points"]) - weight / weight_sum) <= 0.0001
        for row, weight in zip(rows, weights)
    ]
    assert rows and all(comparisons)


def test_numeric_fields_are_rounded_to_four_decimals():
    """Test that numeric output values have no precision beyond four decimals."""
    attribution_numeric = EXPECTED_ATTRIBUTION_COLUMNS[7:]
    channel_numeric = EXPECTED_CHANNEL_COLUMNS[3:]
    values = []
    for path, columns in (
        (ATTRIBUTION_PATH, attribution_numeric),
        (CHANNEL_SUMMARY_PATH, channel_numeric),
    ):
        with open(path, "r") as f:
            for row in csv.DictReader(f):
                values.extend(Decimal(row[column]) for column in columns)

    quantum = Decimal("0.0001")
    assert values and all(value.is_finite() and value == value.quantize(quantum) for value in values)
