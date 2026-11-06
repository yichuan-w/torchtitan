`/app/data/` contains tabular exports from several source systems (.csv, .tsv, .txt). They all represent sales transactions but nothing else is consistent. Build `/app/normalize.py` to produce a unified clean dataset.

The output must be a Parquet file at `/app/output/clean.parquet` with exactly these columns:

- `id` (int)
- `order_date` (YYYY-MM-DD)
- `amount` (float)
- `category` (str)
- `region` (str)

Also write `/app/output/summary.json`:
```json
{
  "total_rows": <int>,
  "total_rows_raw": <int>,
  "date_range": {"min": "YYYY-MM-DD", "max": "YYYY-MM-DD"},
  "categories": [<sorted unique str>],
  "regions": [<sorted unique str>],
  "total_amount": <float, 2dp>
}
```

Deduplicate by `id`, keeping the first occurrence in alphabetical filename order. Drop rows only if the `id` is missing or unparseable; nulls in other columns are fine.

Normalize semantically equivalent category labels from different source systems to one shared English category name.

Run with `python3 /app/normalize.py`. Pandas and PyArrow are pre-installed.
