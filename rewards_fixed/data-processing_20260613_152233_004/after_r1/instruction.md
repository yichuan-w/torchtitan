We have a data export from a legacy system at `/app/orders.csv` that was supposed to be a CSV file, but it won't open correctly in any spreadsheet application. The file came from a 20-year-old COBOL system with non-standard formatting.

Convert this file into a structured JSON report at `/app/output.json`. The output must be a JSON object with this structure:

```json
{
  "summary": {
    "total_records": <number of data rows>,
    "total_amount": "<sum of all TotalAmount as string with 2 decimal places>",
    "status_counts": { "<status>": <count>, ... },
    "monthly_revenue": [
      { "month": "YYYY-MM", "revenue": "<total for that month as string with 2 decimal places>", "order_count": <count> },
      ...
    ]
  },
  "orders": [
    // Array of order objects sorted by Status alphabetically, then by OrderID numerically within status
  ],
  "customers": [
    // Array of customer objects sorted alphabetically by CustomerName (case-sensitive, ASCII)
    // Each has: CustomerName, OrderCount, TotalSpend (string with 2 decimal places)
  ]
}
```

Each order object must contain the seven source fields (`OrderID`, `CustomerName`, `OrderDate`, `ShipAddress`, `TotalAmount`, `Status`, and `Notes`). Represent every order field value as a JSON string; use the empty string for a blank field.

Build the `customers` array by grouping every order by `CustomerName`, including an order whose customer name is the empty string.

The `monthly_revenue` array must be sorted chronologically by month (ascending), covering all months present in the data. Each month entry includes the total revenue and number of orders for that calendar month, derived from the normalized OrderDate.

Dates must be normalized to ISO 8601 format (YYYY-MM-DD). All field values must have leading and trailing whitespace removed. Blank lines in the file must be skipped.

The file is readable as text but has several quirks from the legacy system. You'll need to inspect the raw bytes to figure out the format and how to parse it correctly.

Do not modify the original CSV file.
