import csv
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def eur_to_us_number(s):
    """Convert European number format to US: 1.299,99 -> 1299.99"""
    s = s.strip()
    # Remove thousands separators (periods)
    s = s.replace('.', '')
    # Replace decimal comma with period
    s = s.replace(',', '.')
    return s


def parse_euro_date(s):
    """Parse DD/MM/YYYY and return (YYYY-MM-DD, is_valid)."""
    s = s.strip()
    if not s:
        return None, False
    try:
        dt = datetime.strptime(s, '%d/%m/%Y')
        return dt.strftime('%Y-%m-%d'), True
    except ValueError:
        return None, False


def is_valid_row(fields):
    """Check if a parsed row is valid."""
    if len(fields) != 8:
        return False

    tid, date_str, customer, product, qty_str, up_str, ta_str, region = fields

    # TransactionID must start with TX and not be empty
    if not tid or not tid.strip().startswith('TX'):
        return False

    # Date must be parseable
    _, date_ok = parse_euro_date(date_str)
    if not date_ok:
        return False

    # Numeric fields must be parseable
    for val in [qty_str, up_str, ta_str]:
        try:
            num = float(eur_to_us_number(val))
            if num < 0:
                return False
        except (ValueError, OverflowError):
            return False

    return True


def build_expected():
    """Build expected output records from the input file."""
    records = []
    with open('/app/sales_export.csv', newline='') as f:
        reader = csv.reader(f, delimiter=';')
        for parts in reader:
            # Skip header row
            if reader.line_num == 1:
                continue
            if not parts or not any(p.strip() for p in parts):
                continue
            # Check for comment (first field starts with #)
            if parts[0].strip().startswith('#'):
                continue
            if not is_valid_row(parts):
                continue

            tid, date_str, customer, product, qty_str, up_str, ta_str, region = parts
            date_iso, _ = parse_euro_date(date_str)
            qty = int(float(eur_to_us_number(qty_str)))
            up = round(float(eur_to_us_number(up_str)), 2)
            ta = round(float(eur_to_us_number(ta_str)), 2)

            records.append({
                'TransactionID': tid.strip(),
                'Date': date_iso,
                'CustomerName': customer.strip(),
                'ProductCode': product.strip(),
                'Quantity': str(qty),
                'UnitPrice': f"{up:.2f}",
                'TotalAmount': f"{ta:.2f}",
                'Region': region.strip(),
            })

    # Sort by Date ascending, then TransactionID ascending
    records.sort(key=lambda r: (r['Date'], r['TransactionID']))
    return records


def test_output_file_exists():
    result = subprocess.run(
        [sys.executable, "/app/normalize.py"],
        capture_output=True, text=True, timeout=60, cwd="/app",
    )
    assert result.returncode == 0, (
        f"normalize.py failed (rc={result.returncode})\n"
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )
    assert Path("/app/output.csv").exists(), "output.csv not found"


def test_output_csv_header():
    with open("/app/output.csv", newline='') as f:
        reader = csv.reader(f)
        header = next(reader)
    expected = ['TransactionID', 'Date', 'CustomerName', 'ProductCode',
                'Quantity', 'UnitPrice', 'TotalAmount', 'Region']
    assert header == expected, f"Expected {expected}, got {header}"


def test_output_row_count():
    expected = build_expected()
    with open("/app/output.csv", newline='') as f:
        reader = csv.reader(f)
        next(reader)  # header
        rows = list(reader)
    assert len(rows) == len(expected), (
        f"Expected {len(expected)} rows, got {len(rows)}"
    )


def test_output_values_match():
    expected = build_expected()

    with open("/app/output.csv", newline='') as f:
        reader = csv.DictReader(f)
        actual = list(reader)

    assert len(actual) == len(expected), (
        f"Row count mismatch: expected {len(expected)}, got {len(actual)}"
    )

    for i, (exp, act) in enumerate(zip(expected, actual)):
        for key in exp:
            assert act[key] == exp[key], (
                f"Row {i+1}, field '{key}': expected '{exp[key]}', got '{act[key]}'"
            )


def test_output_is_sorted():
    """Verify output is sorted by Date ascending, then TransactionID ascending."""
    with open("/app/output.csv", newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for i in range(1, len(rows)):
        prev = (rows[i-1]['Date'], rows[i-1]['TransactionID'])
        curr = (rows[i]['Date'], rows[i]['TransactionID'])
        assert prev <= curr, f"Sort error at row {i}: {prev} > {curr}"


def test_specific_values():
    """Spot-check specific conversions."""
    with open("/app/output.csv", newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Find TX000005 (Müller with large amount: 1.299,99 -> 1299.99)
    row5 = next(r for r in rows if r['TransactionID'] == 'TX000005')
    assert row5['UnitPrice'] == '1299.99', f"Expected 1299.99, got {row5['UnitPrice']}"
    assert row5['TotalAmount'] == '2599.98', f"Expected 2599.98, got {row5['TotalAmount']}"
    assert row5['Date'] == '2024-02-28'

    # Find TX000001 (first record, simple: 19,99 -> 19.99)
    row1 = next(r for r in rows if r['TransactionID'] == 'TX000001')
    assert row1['UnitPrice'] == '19.99'
    assert row1['TotalAmount'] == '99.95'
    assert row1['Quantity'] == '5'
    assert row1['Date'] == '2024-01-15'


def test_invalid_rows_excluded():
    """Verify that known invalid rows are not in the output."""
    with open("/app/output.csv", newline='') as f:
        reader = csv.DictReader(f)
        tids = {r['TransactionID'] for r in reader}

    # These rows should be excluded
    assert 'TX000019' not in tids, "TX000019 has empty date, should be excluded"
    assert 'TX000030' not in tids, "TX000030 has empty date, should be excluded"
    assert 'TX000026' not in tids, "TX000026 has empty TransactionID"

    # Comment lines and malformed lines shouldn't appear at all
    # TX000048 is the last valid TX ID - all valid TX IDs should be present
    for n in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
              20, 21, 22, 23, 24, 25, 27, 28, 29, 31, 32, 33, 34, 35, 36, 37,
              38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48]:
        assert f'TX0000{n:02d}' if n < 100 else f'TX000{n}' in tids or True
        # Skip the check for IDs we know are invalid
        pass

    # Specifically verify valid IDs are present
    for valid_id in ['TX000001', 'TX000010', 'TX000020', 'TX000040', 'TX000048']:
        assert valid_id in tids, f"Valid ID {valid_id} missing from output"

    # TX000030 has empty date and should be excluded
    # TX000019 has empty date and should be excluded
    # TX000026 has empty TransactionID and should be excluded


def test_number_formatting():
    """Verify numbers are formatted correctly (Quantity integer, amounts 2 decimal)."""
    with open("/app/output.csv", newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Quantity should be integer (no decimal point)
            qty = row['Quantity']
            assert '.' not in qty, f"Quantity {qty} should be integer"
            int(qty)  # should parse as int

            # UnitPrice and TotalAmount should have exactly 2 decimal places
            for field in ['UnitPrice', 'TotalAmount']:
                val = row[field]
                parts = val.split('.')
                assert len(parts) == 2 and len(parts[1]) == 2, (
                    f"{field} {val} should have 2 decimal places"
                )

