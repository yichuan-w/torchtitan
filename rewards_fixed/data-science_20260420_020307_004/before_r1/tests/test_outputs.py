"""Verifier for the transaction deduplication pipeline."""
import csv
import io
import subprocess
from pathlib import Path


INPUT = Path("/app/data/transactions.csv")
OUTPUT = Path("/app/output/deduped.csv")
COLUMNS = {"transaction_id", "customer_id", "timestamp", "amount"}
EXPECTED_ROWS = {
    ("tx013", "C001", "2024-01-19T16:30:00-08:00", "275.50"),
    ("tx018", "C002", "2024-01-21T08:30:00-08:00", "325.00"),
    ("tx015", "C003", "2024-01-20T11:00:00-06:00", "500.00"),
    ("tx021", "C004", "2024-01-21T08:30:00-08:00", "610.00"),
    ("tx014", "C005", "2024-01-20T09:45:00+01:00", "150.25"),
    ("tx017", "C006", "2024-01-20T17:00:00-08:00", "175.75"),
    ("tx016", "C007", "2024-01-20T14:30:00Z", "225.00"),
    ("tx019", "C008", "2024-01-21T11:45:00Z", "475.00"),
}


def read_output():
    with OUTPUT.open(newline="") as output_file:
        reader = csv.DictReader(output_file)
        return set(reader.fieldnames or []), list(reader)


def test_pipeline_output():
    """The stated invocation must produce exactly the latest source records."""
    original_input = INPUT.read_bytes()
    synthetic_input = io.StringIO(newline="")
    writer = csv.writer(synthetic_input)
    writer.writerow(("transaction_id", "customer_id", "timestamp", "amount"))
    writer.writerow(("probe-old", "probe-customer", "2025-03-02T01:00:00+10:00", "11.25"))
    writer.writerow(("probe-latest", "probe-customer", "2025-03-01T23:30:00Z", "98.75"))
    try:
        INPUT.write_text(synthetic_input.getvalue())
        OUTPUT.unlink(missing_ok=True)
        subprocess.run(["python", "/app/dedupe.py"], check=True, timeout=30)
        probe_columns, probe_rows = read_output()
    finally:
        INPUT.write_bytes(original_input)

    OUTPUT.unlink(missing_ok=True)
    subprocess.run(["python", "/app/dedupe.py"], check=True, timeout=30)
    fieldnames, rows = read_output()

    observed_rows = [
        tuple(row.get(column) for column in ("transaction_id", "customer_id", "timestamp", "amount"))
        for row in rows
    ]
    observed_customers = [row[1] for row in observed_rows]

    assert (
        probe_columns == COLUMNS
        and probe_rows == [{
            "transaction_id": "probe-latest",
            "customer_id": "probe-customer",
            "timestamp": "2025-03-01T23:30:00Z",
            "amount": "98.75",
        }]
        and
        fieldnames == COLUMNS
        and len(observed_rows) == len(EXPECTED_ROWS)
        and len(observed_customers) == len(set(observed_customers))
        and set(observed_rows) == EXPECTED_ROWS
    ), "Output must contain each customer's latest-instant input record once, with all four fields preserved"
