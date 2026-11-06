import csv
import struct
from datetime import date, timedelta
from pathlib import Path


def test_output_file_exists():
    """Verify the output file exists."""
    output_path = Path("/app/inventory.csv")
    assert output_path.exists(), f"Output file {output_path} does not exist"


def test_output_parseable_by_csv_reader():
    """Verify the output can be parsed by Python's csv.reader without errors."""
    output_path = Path("/app/inventory.csv")
    with open(output_path, "r", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    assert len(rows) > 1, "Output file should have at least 2 rows (header + data)"


def test_row_count():
    """Verify exactly 11 rows: 1 header + 10 data rows."""
    output_path = Path("/app/inventory.csv")
    with open(output_path, "r", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    assert len(rows) == 11, (
        f"Expected 11 rows (1 header + 10 data), got {len(rows)}"
    )


def test_column_count():
    """Verify every row has exactly 5 columns."""
    output_path = Path("/app/inventory.csv")
    with open(output_path, "r", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    for i, row in enumerate(rows):
        assert len(row) == 5, (
            f"Row {i} has {len(row)} columns, expected 5. Row content: {row}"
        )


def test_header():
    """Verify the header row has correct column names."""
    output_path = Path("/app/inventory.csv")
    with open(output_path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    expected_header = ["id", "name", "price", "quantity", "date"]
    assert header == expected_header, (
        f"Expected header {expected_header}, got {header}"
    )


def test_data_sorted():
    """Verify rows are sorted by id ascending."""
    output_path = Path("/app/inventory.csv")
    with open(output_path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    ids = [int(row[0]) for row in rows]
    assert ids == sorted(ids), (
        f"Rows are not sorted by id. Got: {ids}"
    )


def test_data_content():
    """Verify the data rows match the binary file content."""
    output_path = Path("/app/inventory.csv")
    with open(output_path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    data_by_id = {}
    for row in rows:
        data_by_id[int(row[0])] = row

    # Parse the binary file to get expected values
    base_date = date(2020, 1, 1)
    expected = []
    with open("/app/inventory.bin", "rb") as f:
        magic = f.read(4)
        assert magic == b"BIN2", f"Bad magic: {magic}"
        count = struct.unpack(">H", f.read(2))[0]
        for _ in range(count):
            rid = struct.unpack(">I", f.read(4))[0]
            name_len = struct.unpack(">B", f.read(1))[0]
            name = f.read(name_len).decode("ascii")
            price = struct.unpack(">f", f.read(4))[0]
            qty = struct.unpack(">I", f.read(4))[0]
            days = struct.unpack(">I", f.read(4))[0]
            dt = base_date + timedelta(days=days)
            expected.append((
                str(rid), name, f"{price:.2f}", str(qty), dt.strftime("%Y-%m-%d")
            ))

    assert len(data_by_id) == len(expected), (
        f"Expected {len(expected)} data rows, got {len(data_by_id)}"
    )

    for exp_id, exp_name, exp_price, exp_qty, exp_date in expected:
        assert int(exp_id) in data_by_id, (
            f"Missing record with id={exp_id}"
        )
        row = data_by_id[int(exp_id)]
        assert row[1] == exp_name, (
            f"ID {exp_id}: expected name '{exp_name}', got '{row[1]}'"
        )
        assert row[2] == exp_price, (
            f"ID {exp_id}: expected price '{exp_price}', got '{row[2]}'"
        )
        assert row[3] == exp_qty, (
            f"ID {exp_id}: expected quantity '{exp_qty}', got '{row[3]}'"
        )
        assert row[4] == exp_date, (
            f"ID {exp_id}: expected date '{exp_date}', got '{row[4]}'"
        )