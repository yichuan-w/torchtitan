We have sales data from three regional offices, but each office exported their data in a different format. The data files are in `/app/data/`:

- `north_sales.csv` — comma-separated, standard format
- `south_sales.csv` — semicolon-separated, different column names and character encoding
- `east_sales.tsv` — tab-separated, some quirks

There are known data quality issues across these files — things like encoding problems, inconsistent date formats, duplicate rows, missing values, and formatting inconsistencies. I need you to figure out what's wrong with each file and fix it.

**What I need:**

1. Clean and normalize all three files, then merge them into a single DuckDB database at `/app/sales.db` with a table called `sales`. The table should have these columns: `order_id`, `customer_id`, `order_date` (as DATE), `product`, `quantity` (as INTEGER), `unit_price` (as FLOAT), and `region`.

   - Map the column names from each file to these standard names.
   - Preserve product names as they appear in the source files (do not translate them).
   - Normalize all dates to YYYY-MM-DD format.
   - Remove any duplicate rows (rows where all fields are identical).
   - Strip whitespace from text fields.
   - Keep all rows, even those with missing values in some fields.

2. Once the database is ready, run these analytical queries and save the results as CSV files in `/app/output/`:

   - **Revenue by region** → `/app/output/revenue_by_region.csv`  
     Total revenue (SUM of quantity × unit_price) per region, sorted highest to lowest. Columns: `region`, `total_revenue`

   - **Top 5 products** → `/app/output/top_products.csv`  
     Products with highest total quantity sold, top 5 only. Columns: `product`, `total_quantity`

   - **Monthly revenue trend** → `/app/output/monthly_revenue.csv`  
     Total revenue per month (YYYY-MM format) across all regions, sorted chronologically. Columns: `month`, `total_revenue`

   - **Average order value** → `/app/output/avg_order_value.csv`  
     Average order value (total revenue / number of orders) per region. Columns: `region`, `avg_order_value`

Create the `/app/output/` directory if it doesn't exist.