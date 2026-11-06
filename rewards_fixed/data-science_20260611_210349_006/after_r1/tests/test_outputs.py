"""Tests for the data normalization task.

These tests verify that normalize.py correctly:
1. Reads all files from /app/data/ (9 files: CSV, TSV, pipe-delimited, including one headerless file)
2. Maps heterogeneous column names to a canonical schema
3. Normalizes dates to ISO format (YYYY-MM-DD)
4. Cleans numeric amounts (removes currency symbols, handles European format)
5. Normalizes category names (maps accented/French/spelled-out forms to canonical)
6. Maps missing values (empty, N/A) to None
7. Deduplicates rows by id (keeping first occurrence by alphabetical filename order)
8. Outputs clean.parquet and summary.json
"""
import json
import os
import subprocess
import sys

import pytest

NORMALIZE_SCRIPT = "/app/normalize.py"
OUTPUT_DIR = "/app/output"
PARQUET_PATH = os.path.join(OUTPUT_DIR, "clean.parquet")
SUMMARY_PATH = os.path.join(OUTPUT_DIR, "summary.json")
DATA_DIR = "/app/data"

# Expected values (deterministic from seed=42 data generation, 9 files)
EXPECTED_TOTAL_ROWS = 1450
EXPECTED_TOTAL_RAW = 4500
EXPECTED_CATEGORIES = ["Books", "Clothing", "Electronics", "Food", "Sports"]
EXPECTED_REGIONS = ["Central", "East", "North", "South", "West"]
EXPECTED_DATE_MIN = "2023-01-01"
EXPECTED_DATE_MAX = "2023-12-29"
EXPECTED_TOTAL_AMOUNT = 3703263.88
EXPECTED_COLUMNS = ["id", "order_date", "amount", "category", "region"]


def _run_normalize():
    """Run the normalize script and return the result."""
    result = subprocess.run(
        [sys.executable, NORMALIZE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/app",
    )
    return result


class TestNormalizeScript:
    """Tests for the normalize.py script outputs."""

    def test_script_exists(self):
        """Verify normalize.py exists at the expected path."""
        assert os.path.exists(NORMALIZE_SCRIPT), (
            f"Expected script not found: {NORMALIZE_SCRIPT}"
        )

    def test_script_runs_successfully(self):
        """Run normalize.py and verify it exits with code 0."""
        result = _run_normalize()
        assert result.returncode == 0, (
            f"normalize.py failed with exit code {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_output_directory_created(self):
        """Verify /app/output/ was created."""
        assert os.path.isdir(OUTPUT_DIR), (
            f"Output directory not created: {OUTPUT_DIR}"
        )

    def test_parquet_file_exists(self):
        """Verify clean.parquet was created."""
        _run_normalize()
        assert os.path.exists(PARQUET_PATH), (
            f"Parquet output not found: {PARQUET_PATH}"
        )

    def test_summary_file_exists(self):
        """Verify summary.json was created."""
        _run_normalize()
        assert os.path.exists(SUMMARY_PATH), (
            f"Summary JSON not found: {SUMMARY_PATH}"
        )

    def test_parquet_schema(self):
        """Verify the parquet file has the correct schema."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        # Check column names
        actual_cols = list(df.columns)
        assert actual_cols == EXPECTED_COLUMNS, (
            f"Expected columns {EXPECTED_COLUMNS}, got {actual_cols}"
        )

        # Check types
        assert df['id'].dtype in ('int64', 'int32'), (
            f"Expected int dtype for 'id', got {df['id'].dtype}"
        )
        assert df['amount'].dtype in ('float64', 'float32'), (
            f"Expected float dtype for 'amount', got {df['amount'].dtype}"
        )

    def test_no_duplicate_ids(self):
        """Verify there are no duplicate IDs in the output."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        dupes = df[df.duplicated(subset='id', keep=False)]
        assert len(dupes) == 0, (
            f"Found {len(dupes)} duplicate IDs in output. "
            f"Sample duplicates: {dupes['id'].head(10).tolist()}"
        )

    def test_row_counts(self):
        """Verify the deduplicated row count is correct."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        assert len(df) == EXPECTED_TOTAL_ROWS, (
            f"Expected {EXPECTED_TOTAL_ROWS} rows after dedup, got {len(df)}"
        )

    def test_date_format(self):
        """Verify all dates are in ISO format (YYYY-MM-DD) and within expected range."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        # Check format: YYYY-MM-DD
        date_series = df['order_date'].dropna()
        import re
        bad_dates = date_series[~date_series.astype(str).str.match(r'^\d{4}-\d{2}-\d{2}$')]
        assert len(bad_dates) == 0, (
            f"Found {len(bad_dates)} dates not in ISO format. "
            f"Examples: {bad_dates.head(5).tolist()}"
        )

        # Check range
        dates = pd.to_datetime(date_series)
        assert dates.min().strftime('%Y-%m-%d') == EXPECTED_DATE_MIN, (
            f"Expected min date {EXPECTED_DATE_MIN}, got {dates.min().strftime('%Y-%m-%d')}"
        )
        assert dates.max().strftime('%Y-%m-%d') == EXPECTED_DATE_MAX, (
            f"Expected max date {EXPECTED_DATE_MAX}, got {dates.max().strftime('%Y-%m-%d')}"
        )

    def test_amounts_are_clean(self):
        """Verify amounts are clean floats (no currency symbols, no commas)."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        amounts = df['amount'].dropna()
        assert len(amounts) > 0, "No non-null amounts found"

        # All should be positive
        assert (amounts > 0).all(), (
            f"Found non-positive amounts: {amounts[amounts <= 0].head(5).tolist()}"
        )

        # All should be floats
        assert amounts.dtype in ('float64', 'float32'), (
            f"Expected float dtype for amounts, got {amounts.dtype}"
        )

    def test_categories_normalized(self):
        """Verify all categories are from the canonical set."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        cats = df['category'].dropna().unique()
        for cat in cats:
            assert cat in EXPECTED_CATEGORIES, (
                f"Unexpected category: '{cat}'. Expected one of {EXPECTED_CATEGORIES}"
            )

    def test_regions_normalized(self):
        """Verify all regions are from the expected set."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        regions = df['region'].dropna().unique()
        for reg in regions:
            assert reg in EXPECTED_REGIONS, (
                f"Unexpected region: '{reg}'. Expected one of {EXPECTED_REGIONS}"
            )

    def test_summary_json_structure(self):
        """Verify summary.json has all required keys with correct types."""
        _run_normalize()

        with open(SUMMARY_PATH, 'r') as f:
            summary = json.load(f)

        required_keys = ['total_rows', 'total_rows_raw', 'date_range',
                         'categories', 'regions', 'total_amount']
        for key in required_keys:
            assert key in summary, f"Missing key '{key}' in summary.json"

        assert isinstance(summary['total_rows'], int), (
            f"total_rows should be int, got {type(summary['total_rows'])}"
        )
        assert isinstance(summary['total_rows_raw'], int), (
            f"total_rows_raw should be int, got {type(summary['total_rows_raw'])}"
        )
        assert isinstance(summary['date_range'], dict), (
            f"date_range should be dict, got {type(summary['date_range'])}"
        )
        assert 'min' in summary['date_range'], "date_range missing 'min'"
        assert 'max' in summary['date_range'], "date_range missing 'max'"
        assert isinstance(summary['categories'], list), (
            f"categories should be list, got {type(summary['categories'])}"
        )
        assert isinstance(summary['regions'], list), (
            f"regions should be list, got {type(summary['regions'])}"
        )
        assert isinstance(summary['total_amount'], (int, float)), (
            f"total_amount should be numeric, got {type(summary['total_amount'])}"
        )

    def test_summary_json_values(self):
        """Verify summary.json values match the parquet data."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        with open(SUMMARY_PATH, 'r') as f:
            summary = json.load(f)

        # Row counts
        assert summary['total_rows'] == EXPECTED_TOTAL_ROWS, (
            f"Expected total_rows={EXPECTED_TOTAL_ROWS}, got {summary['total_rows']}"
        )
        assert summary['total_rows_raw'] == EXPECTED_TOTAL_RAW, (
            f"Expected total_rows_raw={EXPECTED_TOTAL_RAW}, got {summary['total_rows_raw']}"
        )

        # Date range
        assert summary['date_range']['min'] == EXPECTED_DATE_MIN, (
            f"Expected date_range.min={EXPECTED_DATE_MIN}, "
            f"got {summary['date_range']['min']}"
        )
        assert summary['date_range']['max'] == EXPECTED_DATE_MAX, (
            f"Expected date_range.max={EXPECTED_DATE_MAX}, "
            f"got {summary['date_range']['max']}"
        )

        # Categories (sorted)
        assert summary['categories'] == EXPECTED_CATEGORIES, (
            f"Expected categories={EXPECTED_CATEGORIES}, got {summary['categories']}"
        )

        # Regions (sorted)
        assert summary['regions'] == EXPECTED_REGIONS, (
            f"Expected regions={EXPECTED_REGIONS}, got {summary['regions']}"
        )

        # Total amount
        assert abs(summary['total_amount'] - EXPECTED_TOTAL_AMOUNT) < 0.02, (
            f"Expected total_amount≈{EXPECTED_TOTAL_AMOUNT}, "
            f"got {summary['total_amount']}"
        )

    def test_cross_check_summary_vs_parquet(self):
        """Verify summary.json values are consistent with the parquet data."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)

        with open(SUMMARY_PATH, 'r') as f:
            summary = json.load(f)

        # Row count in summary should match parquet
        assert summary['total_rows'] == len(df), (
            f"summary total_rows={summary['total_rows']} but parquet has {len(df)} rows"
        )

        # Categories in summary should match parquet
        parquet_cats = sorted(df['category'].dropna().unique().tolist())
        assert summary['categories'] == parquet_cats, (
            f"summary categories={summary['categories']} but parquet has {parquet_cats}"
        )

        # Regions in summary should match parquet
        parquet_regs = sorted(df['region'].dropna().unique().tolist())
        assert summary['regions'] == parquet_regs, (
            f"summary regions={summary['regions']} but parquet has {parquet_regs}"
        )

        # Total amount should match (within rounding)
        parquet_total = round(float(df['amount'].sum()), 2)
        assert abs(summary['total_amount'] - parquet_total) < 0.02, (
            f"summary total_amount={summary['total_amount']} but parquet sum={parquet_total}"
        )

    def test_id_coverage(self):
        """Verify that IDs from all source files are present in the output."""
        import pandas as pd

        _run_normalize()
        df = pd.read_parquet(PARQUET_PATH)
        output_ids = set(df['id'].tolist())

        # All known ID column names across all files
        id_col_names = {'id', 'order id', 'record number', 'trans_id',
                        'record_id', 'txn_no', 'ordrenr', 'ref_num'}

        # Read all source files and check their IDs appear in output
        all_source_ids = set()
        for fname in sorted(os.listdir(DATA_DIR)):
            if not (fname.endswith('.csv') or fname.endswith('.tsv') or fname.endswith('.txt')):
                continue
            fpath = os.path.join(DATA_DIR, fname)

            # Detect delimiter
            with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                first_line = f.readline()
            if '|' in first_line and first_line.count('|') >= 3:
                sep = '|'
            elif '\t' in first_line:
                sep = '\t'
            elif ';' in first_line and first_line.count(';') > first_line.count(','):
                sep = ';'
            else:
                sep = ','

            # Try UTF-8, fall back to latin-1
            try:
                raw = pd.read_csv(fpath, sep=sep)
            except UnicodeDecodeError:
                raw = pd.read_csv(fpath, encoding='latin-1', sep=sep)

            # Find the ID column
            id_col = None
            for col in raw.columns:
                col_lower = col.lower().strip().lstrip('\ufeff')
                if col_lower in id_col_names:
                    id_col = col
                    break
            if id_col:
                all_source_ids.update(raw[id_col].dropna().astype(int).tolist())

        # Every source ID should be in the output
        missing = all_source_ids - output_ids
        assert len(missing) == 0, (
            f"Found {len(missing)} IDs from source files missing in output. "
            f"Examples: {sorted(list(missing))[:5]}"
        )

    def test_output_is_deterministic(self):
        """Verify running normalize.py twice produces the same output."""
        import hashlib
        import pandas as pd

        # First run
        _run_normalize()
        df1 = pd.read_parquet(PARQUET_PATH)

        # Second run
        _run_normalize()
        df2 = pd.read_parquet(PARQUET_PATH)

        # Compare
        assert len(df1) == len(df2), (
            f"Non-deterministic: first run had {len(df1)} rows, "
            f"second run had {len(df2)} rows"
        )
        assert df1['id'].tolist() == df2['id'].tolist(), (
            "Non-deterministic: ID lists differ between runs"
        )