import csv
import io
from pathlib import Path


def _read_output():
    output_path = Path("/app/output/sales_report.csv")
    assert output_path.exists(), f"Output file {output_path} does not exist"
    content = output_path.read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) >= 1, "Output CSV is empty"
    return rows[0], rows[1:]


def _find_row(data_rows, store_id, product):
    for row in data_rows:
        if row[0] == store_id and row[1] == product:
            return row
    raise AssertionError(f"Row ({store_id}, {product}) not found")


# --- Structural ---

def test_output_file_exists():
    assert Path("/app/output/sales_report.csv").exists()


def test_header():
    header, _ = _read_output()
    assert header == ["store_id", "product", "total_quantity", "total_revenue",
                       "first_sale", "last_sale", "transaction_count"]


def test_row_count():
    """5 stores × 3 products = 15 rows"""
    _, data_rows = _read_output()
    assert len(data_rows) == 15, f"Expected 15, got {len(data_rows)}"


def test_all_rows_7_fields():
    _, data_rows = _read_output()
    for row in data_rows:
        assert len(row) == 7, f"Row has {len(row)} fields: {row}"


def test_sorted_by_store_then_revenue_desc():
    """Sort: store_id ASC, then total_revenue DESC"""
    _, data_rows = _read_output()
    for i in range(len(data_rows) - 1):
        a, b = data_rows[i], data_rows[i + 1]
        if a[0] == b[0]:
            rev_a = float(a[3])
            rev_b = float(b[3])
            assert rev_a >= rev_b, (
                f"Row {i+2}: same store {a[0]} but revenue {rev_a} < {rev_b}"
            )
        else:
            assert a[0] < b[0], f"Row {i+2}: store {a[0]} should be before {b[0]}"


def test_unique_store_product_pairs():
    _, data_rows = _read_output()
    pairs = [(r[0], r[1]) for r in data_rows]
    assert len(pairs) == len(set(pairs)), f"Duplicate pairs: {pairs}"


# --- Revenue computation spot checks ---

def test_nyc01_widget_a():
    _, data_rows = _read_output()
    row = _find_row(data_rows, "NYC01", "Widget-A")
    assert row[2] == "13", f"total_quantity: {row[2]}"
    assert row[3] == "162.50", f"total_revenue: {row[3]}"
    assert row[4] == "2024-03-15 14:20:05", f"first_sale: {row[4]}"
    assert row[5] == "2024-03-16 10:15:00", f"last_sale: {row[5]}"
    assert row[6] == "4", f"transaction_count: {row[6]}"


def test_lax02_widget_a():
    _, data_rows = _read_output()
    row = _find_row(data_rows, "LAX02", "Widget-A")
    assert row[2] == "10"
    assert row[3] == "125.00"
    assert row[4] == "2024-03-15 14:23:15"
    assert row[5] == "2024-03-16 09:15:00"
    assert row[6] == "4"


def test_chi03_gadget_x():
    _, data_rows = _read_output()
    row = _find_row(data_rows, "CHI03", "Gadget-X")
    assert row[2] == "5"
    assert row[3] == "225.00"
    assert row[4] == "2024-03-15 13:30:00"
    assert row[5] == "2024-03-16 07:30:00"
    assert row[6] == "2"


def test_mia05_widget_b():
    _, data_rows = _read_output()
    row = _find_row(data_rows, "MIA05", "Widget-B")
    assert row[2] == "5"
    assert row[3] == "43.75"
    assert row[4] == "2024-03-15 13:00:00"
    assert row[5] == "2024-03-16 12:00:00"
    assert row[6] == "2"


def test_sea04_widget_b():
    """SEA04 Widget-B: 6+8=14 qty, rev=122.50"""
    _, data_rows = _read_output()
    row = _find_row(data_rows, "SEA04", "Widget-B")
    assert row[2] == "14"
    assert row[3] == "122.50"
    assert row[4] == "2024-03-15 16:30:00"
    assert row[5] == "2024-03-16 11:00:00"
    assert row[6] == "2"
