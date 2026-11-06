A data pipeline at `/app/dedupe.py` is supposed to deduplicate transaction records from `/app/data/transactions.csv`, keeping only the most recent transaction per customer (latest instant in time). The pipeline writes the deduplicated results to `/app/output/deduped.csv`.

Running `python /app/dedupe.py` produces output without errors, but the results are wrong — some customers have older transactions instead of their most recent ones. Fix the pipeline so it correctly keeps only the latest transaction per customer.

The output CSV should have the same columns as the input: `transaction_id`, `customer_id`, `timestamp`, and `amount`. Each `customer_id` should appear exactly once with their most recent transaction, preserving all four field values from that input record.
