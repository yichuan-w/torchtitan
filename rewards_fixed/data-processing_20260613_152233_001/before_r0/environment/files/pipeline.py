#!/usr/bin/env python3
"""
Daily Sales Report Pipeline

Reads daily sales data from CSV files in sales_data/ directory,
merges with product catalog and store reference data,
and produces a summary report of revenue by store and category.
"""

import os
import sys
import pandas as pd


def load_sales_data(data_dir: str) -> pd.DataFrame:
    """Load and combine all sales CSV files from the data directory."""
    all_files = []
    for fname in sorted(os.listdir(data_dir)):
        if fname.endswith('.csv'):
            fpath = os.path.join(data_dir, fname)
            df = pd.read_csv(fpath, dtype={'store_id': str})
            all_files.append(df)
    if not all_files:
        raise FileNotFoundError(f"No CSV files found in {data_dir}")
    combined = pd.concat(all_files, ignore_index=True)
    return combined


def validate_sales_data(df: pd.DataFrame) -> pd.DataFrame:
    """Basic validation of sales data integrity."""
    # Remove rows with missing critical fields
    df = df.dropna(subset=['date', 'store_id', 'product_id', 'quantity'])
    # Ensure quantity is positive
    df = df[df['quantity'] > 0]
    # Ensure unit_price is reasonable
    df = df[df['unit_price'] > 0]
    return df


def parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Convert date strings to datetime objects.

    The sales system records dates in a standard format and pandas
    handles the parsing automatically.
    """
    df['date'] = pd.to_datetime(df['date'])
    return df


def add_day_of_week(df: pd.DataFrame) -> pd.DataFrame:
    """Add day-of-week information for reporting purposes."""
    df['day_of_week'] = df['date'].dt.day_name()
    return df


def deduplicate_sales(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove duplicate entries for the same store, date and product.
    When the same transaction appears multiple times (e.g., from
    re-uploads), keep the first occurrence since it's the original.
    """
    df = df.drop_duplicates(
        subset=['store_id', 'date', 'product_id'], keep='first'
    )
    return df


def merge_with_catalog(df: pd.DataFrame, products_df: pd.DataFrame) -> pd.DataFrame:
    """Merge sales data with product catalog to get category info and list prices."""
    df = df.merge(products_df, on='product_id', how='left', suffixes=('_sale', '_list'))
    return df


def check_category_coverage(df: pd.DataFrame) -> pd.DataFrame:
    """Log any products that couldn't be matched to a category."""
    unmatched = df[df['category'].isna()]
    if len(unmatched) > 0:
        print(f"  Warning: {len(unmatched)} rows with unmatched categories")
    return df


def normalize_store_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Convert internal store IDs to the standard reference format."""
    # The sales system uses short numeric IDs, reference uses 'S00x' format
    df['store_id'] = 'S00' + df['store_id'].astype(str)
    return df


def merge_with_stores(df: pd.DataFrame, stores_df: pd.DataFrame) -> pd.DataFrame:
    """Merge with store reference to get store names and regions."""
    df = normalize_store_ids(df)
    df = df.merge(stores_df, on='store_id', how='left')
    return df


def filter_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove outlier transactions that could skew the report.
    Items with unit_price above 80.0 are likely data entry errors
    or special orders that shouldn't be in the daily summary.
    """
    before = len(df)
    df = df[df['unit_price_sale'] <= 80.0]
    removed = before - len(df)
    if removed > 0:
        print(f"  Filtered out {removed} outlier transactions")
    return df


def compute_revenue_column(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate revenue for each transaction."""
    # Use the product catalog list price for consistent valuation
    df['revenue'] = df['quantity'] * df['unit_price_list']
    return df


def build_summary_report(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a pivot table showing daily revenue per store per category.
    Groups by store and date, with categories as columns.
    """
    summary = pd.pivot_table(
        df,
        values='revenue',
        index=['store_id', 'store_name', 'region', 'date'],
        columns='category',
        aggfunc='mean',
        fill_value=0
    )
    summary = summary.reset_index()
    # Sort for consistent output
    summary = summary.sort_values(['store_id', 'date']).reset_index(drop=True)
    return summary


def main():
    data_dir = '/app/sales_data'
    products_path = '/app/products.csv'
    stores_path = '/app/stores.csv'
    output_path = '/app/daily_revenue_report.csv'

    # Load data
    print("Loading sales data...")
    sales = load_sales_data(data_dir)
    print(f"  Loaded {len(sales)} sales records")

    # Validate
    print("Validating data...")
    sales = validate_sales_data(sales)

    # Load reference tables
    products = pd.read_csv(products_path)
    stores = pd.read_csv(stores_path)
    print(f"  Products: {len(products)}, Stores: {len(stores)}")

    # Process
    print("Parsing dates...")
    sales = parse_dates(sales)

    print("Adding temporal features...")
    sales = add_day_of_week(sales)

    print("Merging with product catalog...")
    sales = merge_with_catalog(sales, products)
    sales = check_category_coverage(sales)

    print("Deduplicating...")
    sales = deduplicate_sales(sales)

    print("Merging with store reference...")
    sales = merge_with_stores(sales, stores)

    print("Filtering outliers...")
    sales = filter_outliers(sales)

    print("Computing revenue...")
    sales = compute_revenue_column(sales)

    print("Building summary report...")
    report = build_summary_report(sales)

    # Save
    report.to_csv(output_path, index=False)
    print(f"Report saved to {output_path} ({len(report)} rows)")
    print("Done.")


if __name__ == '__main__':
    main()