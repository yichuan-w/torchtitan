The daily sales report pipeline at `/app/pipeline.py` has been producing numbers that don't match what the accounting team sees when they manually tally the raw sales data. The pipeline reads transactions from `/app/sales_data/daily_sales.csv`, merges with product and store reference tables, and writes `/app/daily_revenue_report.csv`.

The report should show total revenue (quantity × unit_price) for each store, each day, broken down by product category. The pipeline runs without errors and the output CSV looks reasonable, but the totals are off.

Dates in the raw sales CSV use day/month/year order.

Note: the raw CSV may contain correction rows — when a cashier enters the wrong quantity, a corrected row for the same store/date/product is appended. Only the latest correction should count.

Your job is to debug `pipeline.py`, identify every issue that causes incorrect output, and fix them all. When you're done, `/app/daily_revenue_report.csv` must contain the correct values. You can verify your work by examining the raw sales data and reference tables to compute what the numbers should be.
