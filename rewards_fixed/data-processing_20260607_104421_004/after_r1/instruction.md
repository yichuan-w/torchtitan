Fix `/app/normalize.py` so that `python3 /app/normalize.py` produces `/app/output.csv` from `/app/sales_export.csv` with the existing output header.

The output must correctly parse the semicolon-delimited input (including quoted fields), omit comments and malformed or incomplete records, convert dates to `YYYY-MM-DD` and European-formatted numbers to decimal notation, and sort records by date then transaction ID. Quantities must be written as integers, and unit prices and total amounts must always have exactly two decimal places.
