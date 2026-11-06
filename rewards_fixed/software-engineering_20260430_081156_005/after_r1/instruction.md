I've been given a binary transaction data file exported from our company's legacy mainframe and need to convert it to CSV format for our new transaction system.

The data is at `/app/data/transactions.dat` and the documentation I have is in `/app/README.txt`.

The records came from two source systems that use different EBCDIC variants; use the documented System ID to distinguish them and determine the variant used by each source.

Write a Python script at `/app/convert.py` that reads the binary file and outputs a CSV file at `/app/output/transactions.csv` with columns: `account_id`, `trans_type`, `amount`, `description`, `status`.

The `trans_type` column should show `CREDIT` or `DEBIT` (not C/D), and `status` should show `ACTIVE`, `PENDING`, or `ERROR` (not A/P/E). Amounts should be in dollars with 2 decimal places.

I tried writing a quick converter but some of the transaction descriptions look garbled. Can you figure out what's going wrong and produce the correct output?

Preserve punctuation that belongs to a description rather than deleting or guessing replacements for it.
