import csv
import os
from pathlib import Path

SUMMARY_PATH = Path("/app/summary.csv")
DATA_PATH = Path("/app/sales_data.csv")


def _read_summary():
    """Read summary.csv and return (headers, list of dict rows)."""
    with open(SUMMARY_PATH, newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        rows = [row for row in reader]
    return headers, rows


def test_summary_file_exists():
    """The summary.csv file must exist."""
    assert SUMMARY_PATH.exists(), (
        f"summary.csv not found at {SUMMARY_PATH}. "
        "The fixed analyze.py must save its output to /app/summary.csv"
    )


def test_correct_columns():
    """The summary CSV must have the required columns in any order."""
    headers, _ = _read_summary()
    required = {"category", "total_revenue", "avg_order_value", "transaction_count", "total_quantity"}
    actual = set(headers)
    missing = required - actual
    assert not missing, (
        f"Missing required columns in summary.csv: {missing}. "
        f"Found columns: {actual}"
    )
    extra = actual - required
    assert not extra, (
        f"Unexpected columns in summary.csv: {extra}. "
        f"Expected only: {required}"
    )


def test_all_transactions_accounted_for():
    """The total transaction_count across all categories must be 150."""
    _, rows = _read_summary()
    total = sum(int(r["transaction_count"]) for r in rows)
    assert total == 150, (
        f"Total transaction_count is {total}, but there are 150 transactions "
        f"in the source data. Some transactions are being dropped or "
        f"double-counted."
    )


def test_category_names_are_clean():
    """Category names must not have leading or trailing whitespace."""
    _, rows = _read_summary()
    for r in rows:
        cat = r["category"]
        assert cat == cat.strip(), (
            f"Category '{cat}' has leading or trailing whitespace. "
            f"All category names must be stripped."
        )


def test_no_duplicate_categories():
    """There must be no duplicate category names (whitespace variants merged)."""
    _, rows = _read_summary()
    categories = [r["category"] for r in rows]
    seen = set()
    for cat in categories:
        assert cat not in seen, (
            f"Duplicate category '{cat}' found in summary. "
            f"Whitespace variants must be merged into a single category."
        )
        seen.add(cat)


# Expected values for the 5 known categories (not the catch-all for missing data)
# avg_order_value = total_revenue / transaction_count
EXPECTED_CATEGORIES = {
    "Electronics": {"total_revenue": 143656.78, "transaction_count": 46, "total_quantity": 167,
                    "avg_order_value": 143656.78 / 46},
    "Sports":       {"total_revenue": 68822.18,  "transaction_count": 18, "total_quantity": 73,
                     "avg_order_value": 68822.18 / 18},
    "Home & Garden":{"total_revenue": 58633.72,  "transaction_count": 19, "total_quantity": 66,
                     "avg_order_value": 58633.72 / 19},
    "Clothing":     {"total_revenue": 32565.36,  "transaction_count": 12, "total_quantity": 40,
                     "avg_order_value": 32565.36 / 12},
    "Books":        {"total_revenue": 28394.04,  "transaction_count": 14, "total_quantity": 51,
                     "avg_order_value": 28394.04 / 14},
}

# Expected values for the catch-all category (formerly missing/NaN/whitespace categories)
MISSING_TOTAL_REVENUE = 105150.82
MISSING_TRANSACTION_COUNT = 41
MISSING_TOTAL_QUANTITY = 128
MISSING_AVG_ORDER_VALUE = 105150.82 / 41


def _find_missing_category(rows):
    """Find the row that corresponds to the formerly-missing/NaN category group."""
    for r in rows:
        if int(r["transaction_count"]) == MISSING_TRANSACTION_COUNT:
            return r
    return None


def test_known_categories_present():
    """The five known categories must be present with correct names and values."""
    _, rows = _read_summary()
    summary_by_cat = {r["category"]: r for r in rows}

    for cat, expected in EXPECTED_CATEGORIES.items():
        actual_row = summary_by_cat.get(cat)
        assert actual_row is not None, (
            f"Category '{cat}' is missing from the summary. "
            f"Found categories: {sorted(summary_by_cat.keys())}"
        )
        assert abs(float(actual_row["total_revenue"]) - expected["total_revenue"]) < 0.02, (
            f"Category '{cat}': expected total_revenue={expected['total_revenue']:.2f}, "
            f"got {actual_row['total_revenue']}"
        )
        assert int(actual_row["transaction_count"]) == expected["transaction_count"], (
            f"Category '{cat}': expected transaction_count={expected['transaction_count']}, "
            f"got {actual_row['transaction_count']}"
        )
        assert int(actual_row["total_quantity"]) == expected["total_quantity"], (
            f"Category '{cat}': expected total_quantity={expected['total_quantity']}, "
            f"got {actual_row['total_quantity']}"
        )
        assert abs(float(actual_row["avg_order_value"]) - expected["avg_order_value"]) < 0.02, (
            f"Category '{cat}': expected avg_order_value={expected['avg_order_value']:.2f}, "
            f"got {actual_row['avg_order_value']}"
        )


def test_missing_category_present_and_correct():
    """The formerly-missing categories must be grouped into one catch-all category."""
    _, rows = _read_summary()
    missing_row = _find_missing_category(rows)
    assert missing_row is not None, (
        f"No category has transaction_count={MISSING_TRANSACTION_COUNT}. "
        f"Expected one catch-all category for the {MISSING_TRANSACTION_COUNT} "
        f"transactions with missing/invalid category data."
    )

    assert abs(float(missing_row["total_revenue"]) - MISSING_TOTAL_REVENUE) < 0.02, (
        f"Catch-all category: expected total_revenue={MISSING_TOTAL_REVENUE:.2f}, "
        f"got {missing_row['total_revenue']}"
    )
    assert int(missing_row["total_quantity"]) == MISSING_TOTAL_QUANTITY, (
        f"Catch-all category: expected total_quantity={MISSING_TOTAL_QUANTITY}, "
        f"got {missing_row['total_quantity']}"
    )
    assert abs(float(missing_row["avg_order_value"]) - MISSING_AVG_ORDER_VALUE) < 0.02, (
        f"Catch-all category: expected avg_order_value={MISSING_AVG_ORDER_VALUE:.2f}, "
        f"got {missing_row['avg_order_value']}"
    )

    # The catch-all label should be non-empty and not one of the known categories
    label = missing_row["category"]
    assert label not in EXPECTED_CATEGORIES, (
        f"Catch-all category label '{label}' collides with a known category. "
        f"Missing data should be grouped under a distinct label."
    )


def test_exactly_six_categories():
    """The summary must have exactly 6 category rows (5 known + 1 catch-all)."""
    _, rows = _read_summary()
    assert len(rows) == 6, (
        f"Expected exactly 6 category rows (5 known + 1 catch-all), "
        f"but got {len(rows)}: {[r['category'] for r in rows]}"
    )