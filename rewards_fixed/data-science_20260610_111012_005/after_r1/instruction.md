The script at `/app/analyze.py` loads transaction data from `/app/sales_data.csv`, computes per-category revenue statistics, and saves the summary to `/app/summary.csv`.

The output is wrong — the totals don't add up and some categories appear multiple times. Fix the bugs in `/app/analyze.py` so it produces a correct summary. The output must go to `/app/summary.csv` with columns: `category`, `total_revenue`, `avg_order_value`, `transaction_count`, `total_quantity`.

Treat each transaction's order value as its price multiplied by its quantity; `avg_order_value` is the average of those transaction values within the category.

All 150 transactions must be accounted for in the output.
