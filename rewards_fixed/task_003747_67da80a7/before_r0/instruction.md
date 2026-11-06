You are a data analyst working with sensitive financial logs. You need to process a raw CSV file, anonymize user information, perform mathematical aggregations, and load the results into a database.

You have been provided with a CSV file at `/home/user/raw_data.csv` containing three columns: `DateString`, `Email`, and `TransactionValue`. 

Your task is to write a C program located at `/home/user/process.c` that accomplishes the following pipeline:

1. **Read and Filter**: Read `/home/user/raw_data.csv`. Use POSIX regular expressions (`<regex.h>`) to validate the `Email` column. Keep only rows where the email strictly matches the pattern: `^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`. Discard any rows with invalid emails.
2. **Data Masking**: For the valid rows, anonymize the email address. Keep the very first character of the email, replace all subsequent characters before the `@` symbol with exactly three asterisks (`***`), and keep the domain intact. For example, `john.doe@example.com` becomes `j***@example.com`.
3. **Grouping and Aggregation**: Group the records by the newly masked email addresses. Mathematically aggregate the data by calculating the total sum of `TransactionValue` for each masked email.
4. **Sorting**: Sort the aggregated results in descending order based on the total `TransactionValue`. If there is a tie, sort alphabetically by the masked email.
5. **Output**: Write the sorted, aggregated data to a new CSV file at `/home/user/clean_data.csv` with the header `MaskedEmail,TotalValue`. Format the `TotalValue` as a floating-point number with exactly 2 decimal places.

After writing the C program:
1. Compile it using `gcc -o /home/user/process /home/user/process.c`.
2. Execute `/home/user/process` to generate `/home/user/clean_data.csv`.
3. Create an SQLite3 database at `/home/user/analytics.db`.
4. Bulk import the `/home/user/clean_data.csv` file into a table named `UserTotals` with columns `MaskedEmail TEXT` and `TotalValue REAL`.

Ensure all file paths and names match exactly as requested. Do not use external C libraries outside of the standard POSIX C library.