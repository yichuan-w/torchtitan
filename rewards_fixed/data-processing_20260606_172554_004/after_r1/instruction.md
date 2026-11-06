The file `/app/data/sales.log` contains point-of-sale transaction records from multiple store locations. The first line is a header naming the columns. Each data line uses pipe-delimited fields:

```
STORE_ID|TIMESTAMP|PRODUCT|QUANTITY|UNIT_PRICE
```

Write a script (any language) that produces `/app/output/sales_report.csv` with these columns:
```
store_id,product,total_quantity,total_revenue,first_sale,last_sale,transaction_count
```

For each unique (store_id, product) pair, aggregate:
- `total_quantity`: sum of all quantities
- `total_revenue`: sum of (quantity × unit_price), rounded to 2 decimal places
- `first_sale`: earliest timestamp in ISO 8601 format (`YYYY-MM-DD HH:MM:SS`)
- `last_sale`: latest timestamp in ISO 8601 format
- `transaction_count`: number of transactions

Sort the output by `store_id` ascending, then by `total_revenue` descending.

Write `total_revenue` in fixed-point notation with exactly two digits after the decimal point.

Each store's records appear in a contiguous block, but timestamps within a store are not necessarily in order.
