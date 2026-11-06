import csv
import os
import sqlite3

import pytest


def _get_db_path():
    return "/app/sales.db"


def test_database_exists():
    """Verify the DuckDB database file exists."""
    db_path = _get_db_path()
    assert os.path.exists(db_path), (
        f"DuckDB database not found at {db_path}"
    )


def test_sales_table_exists():
    """Verify the sales table exists with correct schema."""
    import duckdb
    con = duckdb.connect(_get_db_path())
    try:
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sales'"
        ).fetchall()
        assert len(tables) > 0, "Table 'sales' not found in the database"

        # Check column names and types
        columns = con.execute("DESCRIBE sales").fetchall()
        col_info = {row[0]: row[1] for row in columns}

        required_cols = ["order_id", "customer_id", "order_date", "product", "quantity", "unit_price", "region"]
        for col in required_cols:
            assert col in col_info, f"Missing column '{col}' in sales table"

        # order_date should be DATE type
        date_type = col_info["order_date"].upper()
        assert "DATE" in date_type, (
            f"order_date should be DATE type, got {col_info['order_date']}"
        )

        # quantity should be INTEGER type
        qty_type = col_info["quantity"].upper()
        assert "INT" in qty_type, (
            f"quantity should be INTEGER type, got {col_info['quantity']}"
        )
    finally:
        con.close()


def test_row_count():
    """Verify the correct number of rows after deduplication."""
    import duckdb
    con = duckdb.connect(_get_db_path())
    try:
        count = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        assert count == 50, (
            f"Expected 50 rows after deduplication, got {count}"
        )
    finally:
        con.close()


def test_no_duplicate_orders():
    """Verify no duplicate order_ids exist."""
    import duckdb
    con = duckdb.connect(_get_db_path())
    try:
        dupes = con.execute(
            "SELECT order_id, COUNT(*) as cnt FROM sales "
            "GROUP BY order_id HAVING cnt > 1"
        ).fetchall()
        assert len(dupes) == 0, (
            f"Found duplicate order_ids: {dupes}"
        )
    finally:
        con.close()


def test_revenue_by_region_output():
    """Verify the revenue_by_region.csv output file."""
    output_path = "/app/output/revenue_by_region.csv"
    assert os.path.exists(output_path), (
        f"Output file not found: {output_path}"
    )

    with open(output_path, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 3, f"Expected 3 regions, got {len(rows)}"

    # Check columns
    assert "region" in rows[0], "Missing 'region' column"
    assert "total_revenue" in rows[0], "Missing 'total_revenue' column"

    # Build dict
    results = {r["region"]: float(r["total_revenue"]) for r in rows}

    # Verify each region has correct revenue
    assert results["North"] == pytest.approx(1836.25, rel=0.01), (
        f"North revenue: expected ~1836.25, got {results['North']}"
    )
    assert results["South"] == pytest.approx(1509.92, rel=0.01), (
        f"South revenue: expected ~1509.92, got {results['South']}"
    )
    assert results["East"] == pytest.approx(1298.82, rel=0.01), (
        f"East revenue: expected ~1298.82, got {results['East']}"
    )

    # Verify sorted descending
    revenues = [float(r["total_revenue"]) for r in rows]
    assert revenues == sorted(revenues, reverse=True), (
        "Revenue by region should be sorted highest to lowest"
    )


def test_top_products_output():
    """Verify the top_products.csv output file."""
    output_path = "/app/output/top_products.csv"
    assert os.path.exists(output_path), (
        f"Output file not found: {output_path}"
    )

    with open(output_path, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 5, f"Expected top 5 products, got {len(rows)}"

    assert "product" in rows[0], "Missing 'product' column"
    assert "total_quantity" in rows[0], "Missing 'total_quantity' column"

    # Check sorted descending
    quantities = [int(r["total_quantity"]) for r in rows]
    assert quantities == sorted(quantities, reverse=True), (
        "Top products should be sorted by quantity descending"
    )

    # Verify top 4 are exactly correct
    top4_expected = [
        ("Wireless Mouse", 19),
        ("USB-C Hub", 18),
        ("Keyboard", 12),
        ("Draadloze Muis", 9),
    ]
    for i, (expected_product, expected_qty) in enumerate(top4_expected):
        assert rows[i]["product"] == expected_product, (
            f"Position {i+1}: expected '{expected_product}', got '{rows[i]['product']}'"
        )
        assert int(rows[i]["total_quantity"]) == expected_qty, (
            f"Position {i+1}: expected qty {expected_qty}, got {rows[i]['total_quantity']}"
        )

    # 5th place: should have quantity 5 (tied among Desk Lamp, Monitor 27", Webcam HD)
    assert int(rows[4]["total_quantity"]) == 5, (
        f"5th product should have quantity 5, got {rows[4]['total_quantity']}"
    )
    assert rows[4]["product"] in ("Desk Lamp", 'Monitor 27"', "Webcam HD"), (
        f"5th product should be one of the tied products, got '{rows[4]['product']}'"
    )


def test_monthly_revenue_output():
    """Verify the monthly_revenue.csv output file."""
    output_path = "/app/output/monthly_revenue.csv"
    assert os.path.exists(output_path), (
        f"Output file not found: {output_path}"
    )

    with open(output_path, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 2, f"Expected 2 months, got {len(rows)}"

    assert "month" in rows[0], "Missing 'month' column"
    assert "total_revenue" in rows[0], "Missing 'total_revenue' column"

    results = {r["month"]: float(r["total_revenue"]) for r in rows}

    assert results["2024-01"] == pytest.approx(4206.03, rel=0.01), (
        f"January revenue: expected ~4206.03, got {results.get('2024-01', 'missing')}"
    )
    assert results["2024-02"] == pytest.approx(438.96, rel=0.01), (
        f"February revenue: expected ~438.96, got {results.get('2024-02', 'missing')}"
    )

    # Verify sorted chronologically
    months = [r["month"] for r in rows]
    assert months == sorted(months), (
        "Monthly revenue should be sorted chronologically"
    )


def test_avg_order_value_output():
    """Verify the avg_order_value.csv output file."""
    output_path = "/app/output/avg_order_value.csv"
    assert os.path.exists(output_path), (
        f"Output file not found: {output_path}"
    )

    with open(output_path, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 3, f"Expected 3 regions, got {len(rows)}"

    assert "region" in rows[0], "Missing 'region' column"
    assert "avg_order_value" in rows[0], "Missing 'avg_order_value' column"

    results = {r["region"]: float(r["avg_order_value"]) for r in rows}

    assert results["North"] == pytest.approx(91.81, rel=0.01), (
        f"North avg: expected ~91.81, got {results['North']}"
    )
    assert results["South"] == pytest.approx(100.66, rel=0.01), (
        f"South avg: expected ~100.66, got {results['South']}"
    )
    assert results["East"] == pytest.approx(86.59, rel=0.01), (
        f"East avg: expected ~86.59, got {results['East']}"
    )


def test_date_format_consistency():
    """Verify all dates are in YYYY-MM-DD format."""
    import duckdb
    con = duckdb.connect(_get_db_path())
    try:
        # Try to parse dates - if any are in wrong format, this will fail
        bad_dates = con.execute(
            "SELECT order_id, order_date FROM sales "
            "WHERE order_date IS NULL "
            "OR CAST(order_date AS VARCHAR) NOT LIKE '____-__-__'"
        ).fetchall()
        assert len(bad_dates) == 0, (
            f"Found rows with invalid date format: {bad_dates}"
        )
    finally:
        con.close()


def test_product_names_cleaned():
    """Verify product names are properly cleaned (no trailing spaces, correct encoding)."""
    import duckdb
    con = duckdb.connect(_get_db_path())
    try:
        # Check for trailing spaces
        trailing = con.execute(
            "SELECT DISTINCT product FROM sales WHERE product != TRIM(product)"
        ).fetchall()
        assert len(trailing) == 0, (
            f"Found product names with trailing spaces: {trailing}"
        )

        # Check that accented characters are preserved correctly
        products = [r[0] for r in con.execute(
            "SELECT DISTINCT product FROM sales ORDER BY product"
        ).fetchall()]

        # These should appear with correct accents
        assert "Café Latte Machine" in products, (
            "Missing or incorrectly encoded 'Café Latte Machine'"
        )
        assert "Crème Brûlée Torch" in products, (
            "Missing or incorrectly encoded 'Crème Brûlée Torch'"
        )
    finally:
        con.close()


def test_prices_cleaned():
    """Verify all prices are clean floats without $ prefix."""
    import duckdb
    con = duckdb.connect(_get_db_path())
    try:
        # All prices should be positive (or zero) numeric values
        # Check that all unit_price values are valid floats
        result = con.execute(
            "SELECT COUNT(*) FROM sales WHERE unit_price IS NULL"
        ).fetchone()[0]
        assert result == 0, f"Found {result} rows with NULL unit_price"

        # Verify no $ signs or other formatting in the data
        str_prices = con.execute(
            "SELECT DISTINCT unit_price FROM sales ORDER BY unit_price"
        ).fetchall()
        for (price,) in str_prices:
            assert isinstance(price, float), (
                f"unit_price should be float, got {type(price)}: {price}"
            )
    finally:
        con.close()