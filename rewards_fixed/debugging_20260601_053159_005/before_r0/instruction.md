The file /app/sensor_data.bin contains compressed sensor telemetry data in a custom binary record format. Two of the records have been corrupted in transit and fail their integrity checks.

A small valid example of the format is provided in /app/example.bin (3 uncorrupted, uncompressed records). Study this example to understand the record layout. See /app/README.md for additional hints about the format.

Your task:
1. Reverse-engineer the binary record format from the example
2. Decompress and parse /app/sensor_data.bin
3. Validate each record's integrity and discard corrupted ones
4. Output all valid records as JSON to /app/valid_records.json

Each output record should have fields: "type" (int), "timestamp" (int), "key" (string), "value" (number for metric/numeric types, string otherwise).
