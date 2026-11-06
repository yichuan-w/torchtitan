import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

SUMMARY_PATH = Path("/app/summary.csv")


def _read_summary():
    with open(SUMMARY_PATH, newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def _number(value):
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise AssertionError(f"Expected a numeric value, got {value!r}") from error


KNOWN_CATCH_ALL_SUMMARY = {
    "Electronics": ("143656.78", "46", "167"),
    "Sports": ("68822.18", "18", "73"),
    "Home & Garden": ("58633.72", "19", "66"),
    "Clothing": ("32565.36", "12", "40"),
    "Books": ("28394.04", "14", "51"),
}
CATCH_ALL_VALUES = ("105150.82", "41", "128")

RECOVERED_SUMMARY = {
    "Books": ("42735.03", "18", "65"),
    "Clothing": ("57256.15", "22", "77"),
    "Electronics": ("158406.48", "53", "183"),
    "Home & Garden": ("71582.09", "26", "87"),
    "Sports": ("107243.15", "31", "113"),
}


def _expected_rows(rows):
    categories = {row["category"] for row in rows}
    known = set(KNOWN_CATCH_ALL_SUMMARY)
    if categories == set(RECOVERED_SUMMARY):
        return RECOVERED_SUMMARY
    if len(categories) == 6 and known <= categories:
        catch_all_label = next(iter(categories - known))
        return {**KNOWN_CATCH_ALL_SUMMARY, catch_all_label: CATCH_ALL_VALUES}
    raise AssertionError(
        "Expected either five product-recovered categories or five known categories "
        f"plus one catch-all category, got {sorted(categories)}"
    )


def _check_row(category, actual, expected):
    total, count, quantity = expected
    assert abs(_number(actual["total_revenue"]) - Decimal(total)) < Decimal("0.02"), (
        f"Category {category!r}: expected total_revenue={total}, "
        f"got {actual['total_revenue']}"
    )
    assert _number(actual["transaction_count"]) == Decimal(count), (
        f"Category {category!r}: expected transaction_count={count}, "
        f"got {actual['transaction_count']}"
    )
    assert _number(actual["total_quantity"]) == Decimal(quantity), (
        f"Category {category!r}: expected total_quantity={quantity}, "
        f"got {actual['total_quantity']}"
    )
    expected_average = Decimal(total) / Decimal(count)
    assert abs(_number(actual["avg_order_value"]) - expected_average) <= Decimal("0.005"), (
        f"Category {category!r}: expected avg_order_value={expected_average}, "
        f"got {actual['avg_order_value']}"
    )


def test_summary_file_exists():
    assert SUMMARY_PATH.exists(), (
        f"summary.csv not found at {SUMMARY_PATH}. "
        "The fixed analyze.py must save its output to /app/summary.csv"
    )


def test_correct_columns():
    headers, _ = _read_summary()
    required = {"category", "total_revenue", "avg_order_value", "transaction_count", "total_quantity"}
    assert set(headers or []) == required, (
        f"Expected exactly columns {required}, found {headers}"
    )


def test_all_transactions_accounted_for():
    _, rows = _read_summary()
    total = sum(_number(row["transaction_count"]) for row in rows)
    assert total == 150, f"Total transaction_count is {total}, expected 150"


def test_category_names_are_clean():
    _, rows = _read_summary()
    for row in rows:
        category = row["category"]
        assert category and category == category.strip(), (
            f"Category {category!r} must be nonempty without surrounding whitespace"
        )


def test_no_duplicate_categories():
    _, rows = _read_summary()
    categories = [row["category"] for row in rows]
    assert len(categories) == len(set(categories)), "Duplicate category rows found"


def test_known_categories_present():
    _, rows = _read_summary()
    expected_rows = _expected_rows(rows)
    actual_rows = {row["category"]: row for row in rows}
    for category, expected in expected_rows.items():
        _check_row(category, actual_rows[category], expected)


def test_missing_categories_accounted_for_correctly():
    _, rows = _read_summary()
    expected_rows = _expected_rows(rows)
    actual_rows = {row["category"]: row for row in rows}
    assert set(actual_rows) == set(expected_rows)
    assert sum(Decimal(values[1]) for values in expected_rows.values()) == 150


def test_valid_category_grouping():
    _, rows = _read_summary()
    expected_rows = _expected_rows(rows)
    assert len(rows) == len(expected_rows)
