We're migrating an old application and have exported log files from multiple legacy systems. The problem is that these files use different character encodings — some are UTF-8 (with and without BOM), some are Latin-1, some are Windows-1252, and some appear to have been processed incorrectly at some point in their lifecycle.

Create a Python script at `/app/normalize_encoding.py` that processes all `.log` files in `/app/logs/` and produces a single consolidated output file at `/app/normalized_output.txt`.

Requirements:
- Detect and handle each file's encoding automatically (UTF-8, UTF-8 with BOM, Latin-1/ISO-8859-1, Windows-1252)
- Handle files where text may have been misinterpreted during previous encoding conversions (common when UTF-8 content was read as single-byte encoding and re-saved)
- Strip any BOM markers from the output
- Handle files where lines within the same file may have different encodings
- Gracefully handle lines with incomplete multi-byte sequences (skip or truncate safely)
- Output all content as clean UTF-8 with Unix line endings (LF only)
- Preserve the order of log entries (process files alphabetically by filename)
- Maintain exact timestamp ordering within each file
- Include a line count summary printed to stdout when complete

The script should work without requiring the user to specify encoding parameters manually. The output should contain all valid log lines with their special characters properly preserved.
