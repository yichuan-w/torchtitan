from pathlib import Path

import pandas as pd


def test_report_exists():
    """Test that the daily revenue report CSV exists."""
    report_path = Path("/app/daily_revenue_report.csv")
    assert report_path.exists(), "/app/daily_revenue_report.csv should exist"
    assert report_path.stat().st_size > 0, "Report file should not be empty"


def test_report_columns():
    """Test that the report has the correct columns."""
    df = pd.read_csv("/app/daily_revenue_report.csv")
    expected_cols = ["store_id", "store_name", "region", "date"]
    category_cols = ["Accessories", "Electronics", "Office", "Stationery"]

    for col in expected_cols:
        assert col in df.columns, f"Report should have column '{col}'"
    for col in category_cols:
        assert col in df.columns, f"Report should have category column '{col}'"


def test_report_row_count():
    """Test that the report has the correct number of rows (40 = 4 stores × 10 days)."""
    df = pd.read_csv("/app/daily_revenue_report.csv")
    assert len(df) == 40, (
        f"Expected 40 rows (4 stores × 10 days), got {len(df)}"
    )


def test_store_names_not_null():
    """Test that all store names are populated (merge worked correctly)."""
    df = pd.read_csv("/app/daily_revenue_report.csv")
    null_names = df["store_name"].isna().sum()
    assert null_names == 0, (
        f"All {null_names} store_name values are NaN — store merge is broken"
    )
    null_regions = df["region"].isna().sum()
    assert null_regions == 0, (
        f"All {null_regions} region values are NaN — store merge is broken"
    )


def test_store_ids_correct():
    """Test that all four stores appear in the report."""
    df = pd.read_csv("/app/daily_revenue_report.csv")
    expected_stores = {"S001", "S002", "S003", "S004"}
    actual_stores = set(df["store_id"].unique())
    assert actual_stores == expected_stores, (
        f"Expected stores {expected_stores}, got {actual_stores}"
    )


def test_dates_are_correct():
    """Test that dates are correctly parsed as DD/MM/YYYY.

    The raw data uses DD/MM/YYYY format. Dates like '03/05/2023' should be
    May 3, 2023, not March 5, 2023.
    """
    df = pd.read_csv("/app/daily_revenue_report.csv")
    df["date"] = pd.to_datetime(df["date"])

    # All dates should be in May-August 2023 (months 5-8)
    for _, row in df.iterrows():
        month = row["date"].month
        year = row["date"].year
        assert year == 2023, (
            f"Date {row['date']} has wrong year: expected 2023, got {year}"
        )
        assert 5 <= month <= 8, (
            f"Date {row['date']} has wrong month: expected 5-8, got {month}"
        )

    # Check specific known dates that are ambiguous
    expected_dates = [
        "2023-05-03", "2023-05-07", "2023-05-12",
        "2023-06-02", "2023-06-08", "2023-06-11",
        "2023-07-05", "2023-07-09", "2023-08-04", "2023-08-10",
    ]
    actual_dates = sorted(df["date"].dt.strftime("%Y-%m-%d").unique())
    assert actual_dates == expected_dates, (
        f"Expected dates {expected_dates}, got {actual_dates}"
    )


def test_revenue_uses_sale_price_not_list_price():
    """Test that revenue is computed using the sale transaction price,
    not the product catalog list price.

    The sales CSV has actual transaction prices which are lower than
    the product catalog list prices. Using list prices would inflate revenue.
    """
    df = pd.read_csv("/app/daily_revenue_report.csv")

    total_accessories = df["Accessories"].sum()
    total_electronics = df["Electronics"].sum()
    total_office = df["Office"].sum()
    total_stationery = df["Stationery"].sum()

    # Correct totals (using sale transaction prices):
    assert abs(total_accessories - 1412.99) < 0.02, (
        f"Accessories total should be 1412.99, got {total_accessories:.2f}. "
        f"Check if you're using product list prices instead of sale prices."
    )
    assert abs(total_electronics - 3758.08) < 0.02, (
        f"Electronics total should be 3758.08, got {total_electronics:.2f}. "
        f"Check if you're using product list prices instead of sale prices."
    )
    assert abs(total_office - 5159.08) < 0.02, (
        f"Office total should be 5159.08, got {total_office:.2f}. "
        f"Check if you're using product list prices instead of sale prices."
    )
    assert abs(total_stationery - 416.45) < 0.02, (
        f"Stationery total should be 416.45, got {total_stationery:.2f}. "
        f"Check if you're using product list prices instead of sale prices."
    )


def test_aggfunc_is_sum_not_mean():
    """Test that revenue values are summed, not averaged.

    If the pivot table uses mean instead of sum, revenue values for
    store-date-category combinations with multiple transactions will be
    too low.
    """
    df = pd.read_csv("/app/daily_revenue_report.csv")
    # With sum, Office total is 5159.08. With mean, it would be much lower.
    total_office = df["Office"].sum()
    # If using mean, office total would be around 2000-3000 at most
    assert total_office > 4500, (
        f"Office total {total_office:.2f} is too low. "
        f"Check if aggfunc is 'mean' instead of 'sum'."
    )


def test_dedup_keeps_corrected_records():
    """Test that deduplication keeps the LAST (corrected) record.

    The sales CSV contains correction entries where a later row corrects
    an earlier one. The last entry for a given (store, date, product)
    should be used.
    """
    df = pd.read_csv("/app/daily_revenue_report.csv")
    df["date"] = pd.to_datetime(df["date"])

    # S001 on 2023-05-03 has P101 (Electronics, sale price 49.99)
    # The corrected quantity is 4 (not the original 1)
    row = df[(df["store_id"] == "S001") & (df["date"] == "2023-05-03")]
    assert len(row) == 1, "Should have exactly one row for S001 on 2023-05-03"

    electronics_val = row.iloc[0]["Electronics"]
    # If dedup kept first (wrong), P101 qty=1 → 1*49.99=49.99 contribution
    # If dedup kept last (correct), P101 qty=4 → 4*49.99=199.96 contribution
    # The total Electronics for S001 on this date should include 199.96 + other items
    assert electronics_val > 250, (
        f"Electronics for S001 on 2023-05-03 should be >250 "
        f"(includes corrected P101 with qty=4), got {electronics_val}"
    )


def test_no_legitimate_sales_filtered_out():
    """Test that all legitimate sales are included, including high-value items.

    The Mechanical Keyboard (P107, sale price=89.99) should be present.
    If a filter removes items with unit_price > 80, Office revenue
    will be too low.
    """
    df = pd.read_csv("/app/daily_revenue_report.csv")
    total_office = df["Office"].sum()
    assert total_office > 4000, (
        f"Office total should be >4000 (includes Mechanical Keyboards), "
        f"got {total_office:.2f}. Check if high-value items are being filtered."
    )


def test_report_has_all_ten_dates():
    """Test that the report covers all 10 days of data."""
    df = pd.read_csv("/app/daily_revenue_report.csv")
    df["date"] = pd.to_datetime(df["date"])
    unique_dates = sorted(df["date"].dt.strftime("%Y-%m-%d").unique())
    assert len(unique_dates) == 10, (
        f"Expected 10 unique dates, got {len(unique_dates)}: {unique_dates}"
    )


def test_each_store_has_ten_days():
    """Test that each store has exactly 10 days of data."""
    df = pd.read_csv("/app/daily_revenue_report.csv")
    for store_id in ["S001", "S002", "S003", "S004"]:
        store_rows = df[df["store_id"] == store_id]
        assert len(store_rows) == 10, (
            f"Store {store_id} should have 10 rows, got {len(store_rows)}"
        )