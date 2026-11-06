#!/usr/bin/env python3
"""
Sales Analytics Pipeline
Processes sales data and generates monthly revenue totals by category.
"""

import pandas as pd
import os

def load_data(filepath):
    """Load sales data from CSV."""
    return pd.read_csv(filepath)

def process_data(df):
    """Process sales data and calculate monthly revenue by category."""
    
    # Parse dates
    df['date'] = pd.to_datetime(df['date'])
    
    # Extract month
    df['month'] = df['date'].dt.strftime('%Y-%m')
    
    # Calculate net revenue (revenue minus discount)
    # Filter to rows with valid discounts
    discounted = df[df['discount'].notna()]
    df['net_revenue'] = df['revenue'] - discounted['discount']
    
    # Fill NaN in net_revenue with original revenue
    df['net_revenue'] = df['net_revenue'].fillna(df['revenue'])
    
    # Group by month and category, sum revenue
    result = df.groupby(['month', 'category'])['net_revenue'].sum().reset_index()
    result.columns = ['month', 'category', 'total_revenue']
    
    # Sort by month and category
    result = result.sort_values(['month', 'category'])
    
    return result

def main():
    # Load data
    df = load_data('/app/data/sales.csv')
    
    # Process data
    result = process_data(df)
    
    # Ensure output directory exists
    os.makedirs('/app/output', exist_ok=True)
    
    # Write results
    result.to_csv('/app/output/monthly_revenue.csv', index=False)
    print("Pipeline completed. Output written to /app/output/monthly_revenue.csv")

if __name__ == '__main__':
    main()
