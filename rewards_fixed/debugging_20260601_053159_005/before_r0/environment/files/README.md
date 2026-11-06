Sensor data files use a custom binary record format. The full file is compressed
with raw DEFLATE (use Python's zlib.decompress with wbits=-15).

An example of the uncompressed format is provided in example.bin.
Study this file to understand the record layout.

Key observations:
- Type 1 and 2 records contain UTF-8 text values
- Type 3 records contain 8-byte IEEE 754 float64 values (little-endian)
- Multi-byte integers are little-endian
- Each record has a 2-byte integrity check at the end

The sensor_data.bin file has been corrupted in transit. Some records may have
damaged value length fields or wrong integrity checks. You'll need to identify
and work around these issues to recover as many valid records as possible.
