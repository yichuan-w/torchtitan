Our sales analytics dashboard is showing incorrect monthly revenue totals by product category. The pipeline in `/app/pipeline.py` processes `/app/data/sales.csv` and writes results to `/app/output/monthly_revenue.csv`, but the numbers don't match what our finance team calculated manually.

The output CSV should have three columns: `month`, `category`, and `total_revenue` - showing the sum of revenue for each month-category combination. Months should be formatted as `YYYY-MM`.

Fix the pipeline so it produces the correct aggregated revenue totals.
