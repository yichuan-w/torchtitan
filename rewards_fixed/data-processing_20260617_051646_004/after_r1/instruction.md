The file `/app/inventory.bin` is a binary file containing 10 product inventory records. Parse it and convert the data to a CSV file at `/app/inventory.csv`.

The file begins with a 4-byte ASCII magic number. The next 2 bytes are an unsigned 16-bit big-endian integer indicating the number of records. You must determine the structure of each record by examining the file.

The CSV output must have columns: id, name, price, quantity, date. The CSV must have a header row and be sorted by id ascending.
- id: the product ID as an integer
- name: the product name (no padding or null bytes)
- price: the unit price formatted to exactly 2 decimal places (e.g., 19.99, 149.50)
- quantity: the quantity as an integer
- date: the date in YYYY-MM-DD format. Its encoded value is a count of days since 2020-01-01.

Hint: Use `xxd` or `od` to inspect the binary file. Python's `struct` module can parse binary data.
