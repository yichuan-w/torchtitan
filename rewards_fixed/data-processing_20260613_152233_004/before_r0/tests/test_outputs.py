import json
from pathlib import Path


def get_output():
    output = Path("/app/output.json")
    assert output.exists(), "output.json does not exist at /app/output.json"
    try:
        return json.loads(output.read_text())
    except json.JSONDecodeError as e:
        raise AssertionError(f"output.json is not valid JSON: {e}")


def get_orders():
    data = get_output()
    assert isinstance(data, dict), f"Expected a JSON object, got {type(data).__name__}"
    assert "orders" in data, "Missing 'orders' key in output"
    assert "summary" in data, "Missing 'summary' key in output"
    return data["orders"], data["summary"]


# --- Structure tests ---

def test_output_is_object():
    """Output must be a JSON object with 'summary' and 'orders' keys."""
    data = get_output()
    assert isinstance(data, dict), f"Expected a JSON object, got {type(data).__name__}"
    assert "summary" in data, "Missing 'summary' key"
    assert "orders" in data, "Missing 'orders' key"


def test_orders_is_array():
    """The 'orders' value must be a JSON array."""
    orders, _ = get_orders()
    assert isinstance(orders, list), f"Expected 'orders' to be an array, got {type(orders).__name__}"


def test_correct_number_of_records():
    """There should be exactly 13 data records (blank lines skipped)."""
    orders, _ = get_orders()
    assert len(orders) == 13, f"Expected 13 records, got {len(orders)}"


def test_required_keys():
    """Every order record must have exactly the 7 required keys."""
    orders, _ = get_orders()
    required_keys = {"OrderID", "CustomerName", "OrderDate", "ShipAddress", "TotalAmount", "Status", "Notes"}
    for i, record in enumerate(orders):
        record_keys = set(record.keys())
        missing = required_keys - record_keys
        extra = record_keys - required_keys
        assert not missing, f"Order {i} missing keys: {missing}"
        assert not extra, f"Order {i} has extra keys: {extra}"


def test_all_values_are_strings():
    """All field values in orders should be strings."""
    orders, _ = get_orders()
    for i, record in enumerate(orders):
        for key, value in record.items():
            assert isinstance(value, str), (
                f"Order {i}, key '{key}': expected string, got {type(value).__name__}: {value!r}"
            )


# --- Sorting tests ---

def test_sorted_by_status_then_orderid():
    """Orders must be sorted by Status alphabetically, then by OrderID numerically."""
    orders, _ = get_orders()
    # Verify Status groups are in alphabetical order
    statuses = [r["Status"] for r in orders]
    # Check that status never decreases
    prev_status = ""
    prev_oid = 0
    for r in orders:
        cur_status = r["Status"]
        cur_oid = int(r["OrderID"])
        if cur_status != prev_status:
            assert cur_status > prev_status, (
                f"Status ordering violated: '{prev_status}' came before '{cur_status}'"
            )
            prev_oid = 0  # reset within-group counter
        else:
            assert cur_oid > prev_oid, (
                f"OrderID ordering violated within status '{cur_status}': "
                f"{prev_oid} came before {cur_oid}"
            )
        prev_status = cur_status
        prev_oid = cur_oid


def test_status_group_order():
    """Status groups must appear in alphabetical order."""
    orders, _ = get_orders()
    seen_statuses = []
    for r in orders:
        if not seen_statuses or r["Status"] != seen_statuses[-1]:
            seen_statuses.append(r["Status"])
    expected = sorted(set(r["Status"] for r in orders))
    assert seen_statuses == expected, (
        f"Status groups not in alphabetical order. Got: {seen_statuses}, expected: {expected}"
    )


# --- Summary tests ---

def test_summary_total_records():
    """summary.total_records must equal 13."""
    orders, summary = get_orders()
    assert summary["total_records"] == 13, (
        f"Expected total_records=13, got {summary['total_records']}"
    )


def test_summary_total_amount():
    """summary.total_amount must be the correct sum as a string with 2 decimal places."""
    orders, summary = get_orders()
    expected = "8811.99"
    assert summary["total_amount"] == expected, (
        f"Expected total_amount='{expected}', got {summary['total_amount']!r}"
    )


def test_summary_status_counts():
    """summary.status_counts must have correct counts for each status."""
    orders, summary = get_orders()
    expected = {
        "Cancelled": 1,
        "Delivered": 2,
        "Pending": 2,
        "Processing": 2,
        "Shipped": 6,
    }
    assert summary["status_counts"] == expected, (
        f"Expected status_counts={expected}, got {summary['status_counts']}"
    )


# --- Edge case tests ---

def test_dates_are_iso8601():
    """All OrderDate values must be in YYYY-MM-DD format."""
    import re
    orders, _ = get_orders()
    iso_date = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    for i, record in enumerate(orders):
        date_val = record.get("OrderDate", "")
        assert iso_date.match(date_val), (
            f"Order {i}: OrderDate '{date_val}' is not in YYYY-MM-DD format"
        )


def test_whitespace_trimmed():
    """No field value should have leading or trailing whitespace."""
    orders, _ = get_orders()
    for i, record in enumerate(orders):
        for key, value in record.items():
            assert value == value.strip(), (
                f"Order {i}, key '{key}': value '{value}' has leading/trailing whitespace"
            )


def test_embedded_newlines_preserved():
    """Record 1002 should have an address with an embedded newline."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1002"), None)
    assert rec is not None, "Record 1002 not found"
    assert "\n" in rec["ShipAddress"], (
        f"Record 1002 ShipAddress should contain embedded newline, got: {rec['ShipAddress']!r}"
    )
    assert "Oak Ave" in rec["ShipAddress"] and "Building B" in rec["ShipAddress"], (
        f"Record 1002 ShipAddress missing expected content: {rec['ShipAddress']!r}"
    )


def test_date_normalization_mm_dd_yyyy():
    """Records with MM/DD/YYYY dates should be normalized to YYYY-MM-DD."""
    orders, _ = get_orders()

    rec_1002 = next((r for r in orders if r.get("OrderID") == "1002"), None)
    assert rec_1002 is not None, "Record 1002 not found"
    assert rec_1002["OrderDate"] == "2024-01-15", (
        f"Record 1002 OrderDate should be '2024-01-15', got {rec_1002['OrderDate']!r}"
    )

    rec_1006 = next((r for r in orders if r.get("OrderID") == "1006"), None)
    assert rec_1006 is not None, "Record 1006 not found"
    assert rec_1006["OrderDate"] == "2024-03-15", (
        f"Record 1006 OrderDate should be '2024-03-15', got {rec_1006['OrderDate']!r}"
    )

    rec_1011 = next((r for r in orders if r.get("OrderID") == "1011"), None)
    assert rec_1011 is not None, "Record 1011 not found"
    assert rec_1011["OrderDate"] == "2024-06-20", (
        f"Record 1011 OrderDate should be '2024-06-20', got {rec_1011['OrderDate']!r}"
    )


def test_embedded_commas_preserved():
    """Record 1001's ShipAddress should contain a comma."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1001"), None)
    assert rec is not None, "Record 1001 not found"
    assert ", " in rec["ShipAddress"], (
        f"Record 1001 ShipAddress should contain comma, got: {rec['ShipAddress']!r}"
    )


def test_empty_fields():
    """Records with missing fields should have empty string values."""
    orders, _ = get_orders()

    rec_1006 = next((r for r in orders if r.get("OrderID") == "1006"), None)
    assert rec_1006 is not None, "Record 1006 not found"
    assert rec_1006["TotalAmount"] == "", (
        f"Record 1006 TotalAmount should be empty, got {rec_1006['TotalAmount']!r}"
    )

    rec_1009 = next((r for r in orders if r.get("OrderID") == "1009"), None)
    assert rec_1009 is not None, "Record 1009 not found"
    assert rec_1009["ShipAddress"] == "", (
        f"Record 1009 ShipAddress should be empty, got {rec_1009['ShipAddress']!r}"
    )

    rec_1003 = next((r for r in orders if r.get("OrderID") == "1003"), None)
    assert rec_1003 is not None, "Record 1003 not found"
    assert rec_1003["Notes"] == "", (
        f"Record 1003 Notes should be empty, got {rec_1003['Notes']!r}"
    )

    rec_1013 = next((r for r in orders if r.get("OrderID") == "1013"), None)
    assert rec_1013 is not None, "Record 1013 not found"
    assert rec_1013["CustomerName"] == "", (
        f"Record 1013 CustomerName should be empty, got {rec_1013['CustomerName']!r}"
    )


def test_extra_trailing_delimiters_ignored():
    """Record 1008 has extra trailing || — Notes should be 'Normal order'."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1008"), None)
    assert rec is not None, "Record 1008 not found"
    assert rec["Notes"] == "Normal order", (
        f"Record 1008 Notes should be 'Normal order', got {rec['Notes']!r}"
    )


def test_backslash_escaped_pipe():
    """Record 1004: CustomerName should contain literal pipe character."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1004"), None)
    assert rec is not None, "Record 1004 not found"
    assert "|" in rec["CustomerName"], (
        f"Record 1004 CustomerName should contain pipe, got: {rec['CustomerName']!r}"
    )


def test_backslash_escaped_quotes():
    """Record 1007: CustomerName should contain literal double-quote characters."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1007"), None)
    assert rec is not None, "Record 1007 not found"
    assert '"' in rec["CustomerName"], (
        f"Record 1007 CustomerName should contain double-quote, got: {rec['CustomerName']!r}"
    )


def test_comma_in_quoted_customer_name():
    """Record 1002: CustomerName should contain a comma (GlobalTech, Ltd.)."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1002"), None)
    assert rec is not None, "Record 1002 not found"
    assert "," in rec["CustomerName"], (
        f"Record 1002 CustomerName should contain comma, got: {rec['CustomerName']!r}"
    )


def test_escaped_backslash():
    """Record 1010: CustomerName should contain a literal backslash."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1010"), None)
    assert rec is not None, "Record 1010 not found"
    assert "\\" in rec["CustomerName"], (
        f"Record 1010 CustomerName should contain backslash, got: {rec['CustomerName']!r}"
    )


def test_record_1010_address_newline():
    """Record 1010: ShipAddress should have embedded newline."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1010"), None)
    assert rec is not None, "Record 1010 not found"
    assert "\n" in rec["ShipAddress"], (
        f"Record 1010 ShipAddress should contain embedded newline, got: {rec['ShipAddress']!r}"
    )


def test_quoted_street_name():
    """Record 1011: ShipAddress should contain literal double-quote characters."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1011"), None)
    assert rec is not None, "Record 1011 not found"
    assert '"' in rec["ShipAddress"], (
        f"Record 1011 ShipAddress should contain double-quote, got: {rec['ShipAddress']!r}"
    )


def test_multiline_notes():
    """Record 1012: Notes should have embedded newline."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1012"), None)
    assert rec is not None, "Record 1012 not found"
    assert "\n" in rec["Notes"], (
        f"Record 1012 Notes should contain embedded newline, got: {rec['Notes']!r}"
    )
    assert "Multi-line" in rec["Notes"] and "notes field" in rec["Notes"], (
        f"Record 1012 Notes missing expected content: {rec['Notes']!r}"
    )


def test_record_1012_address_newline():
    """Record 1012: ShipAddress should have embedded newline."""
    orders, _ = get_orders()
    rec = next((r for r in orders if r.get("OrderID") == "1012"), None)
    assert rec is not None, "Record 1012 not found"
    assert "\n" in rec["ShipAddress"], (
        f"Record 1012 ShipAddress should contain embedded newline, got: {rec['ShipAddress']!r}"
    )


def test_blank_lines_skipped():
    """Blank lines in the CSV should be skipped."""
    orders, _ = get_orders()
    for record in orders:
        oid = record.get("OrderID", "")
        assert oid and oid.isdigit(), (
            f"Found record with invalid OrderID: {oid!r} (blank lines may not have been skipped)"
        )


# --- Customer aggregation tests ---

def test_customers_array_exists():
    """Output must have a 'customers' array."""
    data = get_output()
    assert "customers" in data, "Missing 'customers' key in output"
    assert isinstance(data["customers"], list), (
        f"Expected 'customers' to be an array, got {type(data['customers']).__name__}"
    )


def test_customers_sorted_alphabetically():
    """Customers must be sorted alphabetically by CustomerName."""
    data = get_output()
    customers = data["customers"]
    names = [c["CustomerName"] for c in customers]
    assert names == sorted(names), (
        f"Customers not sorted alphabetically. Got: {names}"
    )


def test_customers_unique():
    """Each CustomerName should appear exactly once in the customers array."""
    data = get_output()
    customers = data["customers"]
    names = [c["CustomerName"] for c in customers]
    assert len(names) == len(set(names)), (
        f"Duplicate customer names found: {names}"
    )


def test_customers_correct_count():
    """There should be exactly 13 unique customers."""
    data = get_output()
    customers = data["customers"]
    assert len(customers) == 13, (
        f"Expected 13 unique customers, got {len(customers)}"
    )


def test_customers_required_keys():
    """Each customer must have CustomerName, OrderCount, TotalSpend."""
    data = get_output()
    customers = data["customers"]
    required = {"CustomerName", "OrderCount", "TotalSpend"}
    for i, c in enumerate(customers):
        keys = set(c.keys())
        assert keys == required, (
            f"Customer {i} has keys {keys}, expected {required}"
        )


def test_customers_order_count_integer():
    """OrderCount must be an integer >= 0."""
    data = get_output()
    for i, c in enumerate(data["customers"]):
        assert isinstance(c["OrderCount"], int), (
            f"Customer {i}: OrderCount should be int, got {type(c['OrderCount']).__name__}"
        )
        assert c["OrderCount"] >= 0, (
            f"Customer {i}: OrderCount should be >= 0, got {c['OrderCount']}"
        )


def test_customers_total_spend_string():
    """TotalSpend must be a string with 2 decimal places."""
    import re
    data = get_output()
    for i, c in enumerate(data["customers"]):
        assert isinstance(c["TotalSpend"], str), (
            f"Customer {i}: TotalSpend should be str, got {type(c['TotalSpend']).__name__}"
        )
        assert re.match(r'^\d+\.\d{2}$', c["TotalSpend"]), (
            f"Customer {i}: TotalSpend '{c['TotalSpend']}' not in format 'XXX.XX'"
        )


def test_customers_total_spend_sum_matches():
    """Sum of all TotalSpend values must equal the summary total_amount."""
    data = get_output()
    total = sum(float(c["TotalSpend"]) for c in data["customers"])
    expected = float(data["summary"]["total_amount"])
    assert abs(total - expected) < 0.01, (
        f"Sum of customer TotalSpend ({total:.2f}) != summary total_amount ({expected:.2f})"
    )


def test_customers_match_orders():
    """Verify customer aggregation matches order data."""
    data = get_output()
    orders = data["orders"]
    customers = {c["CustomerName"]: c for c in data["customers"]}

    # Count orders per customer from orders array
    from collections import defaultdict
    order_counts = defaultdict(int)
    order_totals = defaultdict(float)
    for o in orders:
        name = o["CustomerName"]
        order_counts[name] += 1
        if o["TotalAmount"]:
            order_totals[name] += float(o["TotalAmount"])

    for name, c in customers.items():
        assert c["OrderCount"] == order_counts[name], (
            f"Customer '{name}': OrderCount {c['OrderCount']} != {order_counts[name]}"
        )
        expected_spend = f"{order_totals[name]:.2f}"
        assert c["TotalSpend"] == expected_spend, (
            f"Customer '{name}': TotalSpend {c['TotalSpend']} != {expected_spend}"
        )


# --- Monthly revenue tests ---

def test_monthly_revenue_exists():
    """summary must have a 'monthly_revenue' array."""
    orders, summary = get_orders()
    assert "monthly_revenue" in summary, "Missing 'monthly_revenue' in summary"
    assert isinstance(summary["monthly_revenue"], list), (
        "monthly_revenue must be an array"
    )


def test_monthly_revenue_sorted_chronologically():
    """Monthly revenue entries must be sorted chronologically by month."""
    orders, summary = get_orders()
    mr = summary["monthly_revenue"]
    months = [entry["month"] for entry in mr]
    assert months == sorted(months), (
        f"monthly_revenue not sorted chronologically. Got: {months}"
    )


def test_monthly_revenue_correct_months():
    """Monthly revenue must cover exactly the 8 months with orders."""
    orders, summary = get_orders()
    mr = summary["monthly_revenue"]
    expected_months = [
        "2024-01", "2024-02", "2024-03", "2024-04",
        "2024-05", "2024-06", "2024-07", "2024-08",
    ]
    actual_months = [entry["month"] for entry in mr]
    assert actual_months == expected_months, (
        f"Expected months {expected_months}, got {actual_months}"
    )


def test_monthly_revenue_required_keys():
    """Each monthly entry must have month, revenue, order_count."""
    orders, summary = get_orders()
    required = {"month", "revenue", "order_count"}
    for i, entry in enumerate(summary["monthly_revenue"]):
        keys = set(entry.keys())
        assert keys == required, (
            f"Monthly entry {i} has keys {keys}, expected {required}"
        )


def test_monthly_revenue_values():
    """Verify monthly revenue values match computed values from orders."""
    orders, summary = get_orders()
    mr = summary["monthly_revenue"]

    # Compute expected from orders
    from collections import defaultdict
    monthly_counts = defaultdict(int)
    monthly_revenue = defaultdict(float)
    for o in orders:
        month = o["OrderDate"][:7]  # YYYY-MM
        monthly_counts[month] += 1
        if o["TotalAmount"]:
            monthly_revenue[month] += float(o["TotalAmount"])

    for entry in mr:
        m = entry["month"]
        assert entry["order_count"] == monthly_counts[m], (
            f"Month {m}: order_count {entry['order_count']} != {monthly_counts[m]}"
        )
        expected_rev = f"{monthly_revenue[m]:.2f}"
        assert entry["revenue"] == expected_rev, (
            f"Month {m}: revenue {entry['revenue']} != {expected_rev}"
        )


def test_monthly_revenue_order_count_integer():
    """order_count in monthly entries must be an integer."""
    orders, summary = get_orders()
    for entry in summary["monthly_revenue"]:
        assert isinstance(entry["order_count"], int), (
            f"Month {entry['month']}: order_count should be int, "
            f"got {type(entry['order_count']).__name__}"
        )


def test_monthly_revenue_format():
    """revenue must be a string with 2 decimal places."""
    import re
    orders, summary = get_orders()
    for entry in summary["monthly_revenue"]:
        assert isinstance(entry["revenue"], str), (
            f"Month {entry['month']}: revenue should be str"
        )
        assert re.match(r'^\d+\.\d{2}$', entry["revenue"]), (
            f"Month {entry['month']}: revenue '{entry['revenue']}' not in format 'XXX.XX'"
        )


def test_monthly_revenue_sum_matches():
    """Sum of all monthly revenues must equal total_amount."""
    orders, summary = get_orders()
    mr_total = sum(float(e["revenue"]) for e in summary["monthly_revenue"])
    expected = float(summary["total_amount"])
    assert abs(mr_total - expected) < 0.01, (
        f"Sum of monthly revenues ({mr_total:.2f}) != total_amount ({expected:.2f})"
    )


