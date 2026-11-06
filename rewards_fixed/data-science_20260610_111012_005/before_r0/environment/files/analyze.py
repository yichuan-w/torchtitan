"""Sales Data Analysis Script.

Loads transaction data, computes per-category revenue statistics,
and saves the summary to /app/summary.csv.
"""
import pandas as pd
import sys


def main():
    df = pd.read_csv('/app/sales_data.csv')

    # Convert price to numeric (strip the dollar sign)
    df['price_num'] = pd.to_numeric(
        df['price'].str.replace('$', '', regex=False),
        errors='coerce'
    )

    # Compute revenue per transaction
    df['revenue'] = df['price_num'] * df['quantity']

    # Aggregate per category
    summary = df.groupby('category').agg(
        total_revenue=('revenue', 'sum'),
        avg_order_value=('price_num', 'mean'),
        transaction_count=('revenue', 'count'),
        total_quantity=('quantity', 'sum'),
    ).reset_index()

    # Sort by total_revenue descending
    summary = summary.sort_values('total_revenue', ascending=False)

    summary.to_csv('/app/summary.csv', index=False, float_format='%.2f')
    print("Analysis complete. Summary saved to /app/summary.csv")


if __name__ == '__main__':
    main()