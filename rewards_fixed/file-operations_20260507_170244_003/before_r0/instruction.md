A server backup archive at `/app/backup.tar.gz` was recovered from a failed storage volume. The archive appears to be partially corrupted and truncated — standard extraction tools fail partway through.

Your task is to recover all possible data from this backup and place the recovered files in `/app/recovered/`.

Requirements:
- Extract any files that standard tar can handle
- For files with corrupted headers but intact data, manually carve them from the decompressed tar stream
- The recovered SQLite database must be queryable
- The recovered CSV must have all its data rows intact

The original backup contained application data including a database, image assets, and configuration files. At least one file is lost completely due to truncation — that's expected.

Place recovered files in `/app/recovered/` maintaining their original paths relative to the backup root (e.g., if a file was `data/reports/sales.db` in the archive, it should be `/app/recovered/data/reports/sales.db`).
