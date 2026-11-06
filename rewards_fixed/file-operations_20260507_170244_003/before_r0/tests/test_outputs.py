"""
Tests for corrupted archive recovery task.
Verifies that the solver recovered all possible files from the corrupted backup.
"""
import os
import sqlite3
import subprocess


def test_recovered_directory_exists():
    """Test that the recovered directory was created."""
    assert os.path.isdir('/app/recovered'), "Directory /app/recovered does not exist"


def test_banner_png_recovered():
    """Test that banner.png was extracted and is valid."""
    png_path = '/app/recovered/data/banner.png'
    assert os.path.isfile(png_path), f"File {png_path} not found"
    
    # Check PNG magic bytes
    with open(png_path, 'rb') as f:
        header = f.read(8)
    
    png_signature = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    assert header == png_signature, f"Invalid PNG signature: got {header.hex()}"
    
    # Check file size is reasonable (original was 10362 bytes)
    size = os.path.getsize(png_path)
    assert size == 10362, f"PNG size mismatch: expected 10362, got {size}"


def test_sales_db_recovered_and_queryable():
    """Test that sales.db was carved and is a valid, queryable database."""
    db_path = '/app/recovered/data/reports/sales.db'
    assert os.path.isfile(db_path), f"File {db_path} not found"
    
    # Verify SQLite header
    with open(db_path, 'rb') as f:
        header = f.read(16)
    
    assert header.startswith(b'SQLite format 3'), f"Invalid SQLite header: {header[:16]}"
    
    # Verify database is queryable and has expected data
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check sales table
    cursor.execute("SELECT COUNT(*) FROM sales")
    sales_count = cursor.fetchone()[0]
    assert sales_count == 50, f"Expected 50 rows in sales table, got {sales_count}"
    
    # Check summary table
    cursor.execute("SELECT COUNT(*) FROM summary")
    summary_count = cursor.fetchone()[0]
    assert summary_count == 12, f"Expected 12 rows in summary table, got {summary_count}"
    
    # Verify data integrity - check a specific record
    cursor.execute("SELECT product, quantity FROM sales WHERE id = 1")
    row = cursor.fetchone()
    assert row is not None, "Could not find sales record with id=1"
    assert row[0] == 'Widget A', f"Unexpected product: {row[0]}"
    
    conn.close()


def test_transactions_csv_recovered():
    """Test that transactions.csv was carved with all data intact."""
    csv_path = '/app/recovered/data/reports/transactions.csv'
    assert os.path.isfile(csv_path), f"File {csv_path} not found"
    
    with open(csv_path, 'r') as f:
        content = f.read()
    
    lines = content.strip().split('\n')
    
    # Should have header + 100 data rows = 101 lines
    assert len(lines) == 101, f"Expected 101 lines in CSV, got {len(lines)}"
    
    # Check header
    assert lines[0] == 'transaction_id,product,quantity,price,timestamp,region', \
        f"Invalid CSV header: {lines[0]}"
    
    # Check last line
    expected_last = '1099,Tool Z,96,57.5,2024-04-16,West'
    assert lines[-1] == expected_last, f"Last line mismatch: expected '{expected_last}', got '{lines[-1]}'"


def test_no_extra_config_file():
    """Test that config.json was not fabricated (it was lost in truncation)."""
    json_path = '/app/recovered/data/config.json'
    # This test requires the recovered directory to exist first
    # If it doesn't exist, fail (so this test fails initially)
    if os.path.isdir('/app/recovered'):
        assert not os.path.exists(json_path), \
            f"config.json should not be recoverable from the truncated archive"
    else:
        # Fail if the solver hasn't done any work yet
        assert False, "/app/recovered directory must be created first"


def test_directory_structure():
    """Test that the recovered directory structure is correct."""
    expected_dirs = [
        '/app/recovered/data',
        '/app/recovered/data/reports'
    ]
    
    for dir_path in expected_dirs:
        assert os.path.isdir(dir_path), f"Expected directory {dir_path} not found"
