# Tests for EBCDIC transaction data conversion task
# These tests verify the correct conversion of mainframe EBCDIC data to CSV

import csv
import os
import subprocess


def test_convert_script_exists():
    """Test that the convert.py script was created"""
    assert os.path.exists("/app/convert.py"), "convert.py script not found at /app/convert.py"


def test_output_csv_exists():
    """Test that the output CSV file was created"""
    assert os.path.exists("/app/output/transactions.csv"), \
        "Output CSV not found at /app/output/transactions.csv"


def test_csv_has_correct_headers():
    """Test that the CSV has the correct column headers"""
    with open("/app/output/transactions.csv", "r") as f:
        reader = csv.reader(f)
        headers = next(reader)
    
    expected_headers = ["account_id", "trans_type", "amount", "description", "status"]
    assert headers == expected_headers, \
        f"Expected headers {expected_headers}, got {headers}"


def test_csv_record_count():
    """Test that the CSV has the correct number of records"""
    with open("/app/output/transactions.csv", "r") as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        records = list(reader)
    
    assert len(records) == 10, f"Expected 10 records, got {len(records)}"


def test_cp037_records_decoded_correctly():
    """Test that cp037-encoded records are decoded correctly"""
    with open("/app/output/transactions.csv", "r") as f:
        reader = csv.DictReader(f)
        records = list(reader)
    
    # Records 1-3 are cp037 encoded (indices 0-2)
    # Record 1: ACCT0001, Credit, 125.00, "DEPOSIT INITIAL", ACTIVE
    assert records[0]["account_id"] == "ACCT0001", \
        f"Record 1 account_id wrong: {records[0]['account_id']}"
    assert records[0]["trans_type"] == "CREDIT", \
        f"Record 1 trans_type wrong: {records[0]['trans_type']}"
    assert records[0]["amount"] == "125.00", \
        f"Record 1 amount wrong: {records[0]['amount']}"
    assert records[0]["description"] == "DEPOSIT INITIAL", \
        f"Record 1 description wrong: {records[0]['description']}"
    assert records[0]["status"] == "ACTIVE", \
        f"Record 1 status wrong: {records[0]['status']}"
    
    # Record 2: ACCT0002, Debit, 50.00, "ATM WITHDRAWAL", ACTIVE
    assert records[1]["account_id"] == "ACCT0002"
    assert records[1]["trans_type"] == "DEBIT"
    assert records[1]["amount"] == "50.00"
    assert records[1]["description"] == "ATM WITHDRAWAL"
    assert records[1]["status"] == "ACTIVE"


def test_cp500_records_decoded_correctly():
    """Test that cp500-encoded records (with special chars) are decoded correctly
    
    This tests the key encoding issue: cp500 records contain characters
    like !, [, ], | that decode incorrectly if the wrong code page is used.
    
    The solver must detect that these records use cp500 encoding (System ID 0x02)
    and decode them accordingly.
    """
    with open("/app/output/transactions.csv", "r") as f:
        reader = csv.DictReader(f)
        records = list(reader)
    
    # Record 4 (index 3): ACCT0004, Debit, 750.00, "PAYMENT [URGENT]!", ACTIVE
    # This is a cp500 record - if decoded as cp037, [ becomes ¢ and ! becomes |
    assert records[3]["account_id"] == "ACCT0004", \
        f"Record 4 account_id wrong: {records[3]['account_id']}"
    assert records[3]["trans_type"] == "DEBIT"
    assert records[3]["amount"] == "750.00"
    assert records[3]["description"] == "PAYMENT [URGENT]!", \
        f"Record 4 description wrong (encoding issue?): {records[3]['description']}"
    assert records[3]["status"] == "ACTIVE"
    
    # Record 5 (index 4): ACCT0005, Credit, 250.00, "REFUND|PROCESS", ACTIVE
    # | character in cp500 - decodes as ] if wrong code page used
    assert records[4]["account_id"] == "ACCT0005"
    assert records[4]["amount"] == "250.00"
    assert records[4]["description"] == "REFUND|PROCESS", \
        f"Record 5 description wrong (encoding issue?): {records[4]['description']}"
    
    # Record 7 (index 6): ACCT0007, Credit, 87.50, "BONUS!APPROVED", ACTIVE
    # ! character - decodes as | if wrong code page used
    assert records[6]["account_id"] == "ACCT0007"
    assert records[6]["amount"] == "87.50"
    assert records[6]["description"] == "BONUS!APPROVED", \
        f"Record 7 description wrong (encoding issue?): {records[6]['description']}"


def test_comp3_negative_handling():
    """Test COMP-3 packed decimal sign handling"""
    with open("/app/output/transactions.csv", "r") as f:
        reader = csv.DictReader(f)
        records = list(reader)
    
    # All amounts in our test data are positive
    # Verify they are all positive values with correct decimal placement
    for i, record in enumerate(records):
        amount = float(record["amount"])
        assert amount > 0, f"Record {i+1} has unexpected negative amount: {amount}"


def test_all_amounts_correct():
    """Test that all COMP-3 packed decimal amounts are decoded correctly"""
    expected_amounts = [
        "125.00",   # Record 1: 12500 cents
        "50.00",    # Record 2: 5000 cents
        "1500.00",  # Record 3: 150000 cents
        "750.00",   # Record 4: 75000 cents
        "250.00",   # Record 5: 25000 cents
        "1250.00",  # Record 6: 125000 cents
        "87.50",    # Record 7: 8750 cents
        "33.00",    # Record 8: 3300 cents
        "450.00",   # Record 9: 45000 cents
        "99.99",    # Record 10: 9999 cents
    ]
    
    with open("/app/output/transactions.csv", "r") as f:
        reader = csv.DictReader(f)
        records = list(reader)
    
    for i, (record, expected) in enumerate(zip(records, expected_amounts)):
        assert record["amount"] == expected, \
            f"Record {i+1} amount wrong: expected {expected}, got {record['amount']}"


def test_status_values_expanded():
    """Test that status codes are expanded to full words"""
    expected_statuses = [
        "ACTIVE", "ACTIVE", "PENDING", "ACTIVE", "ACTIVE",
        "ERROR", "ACTIVE", "ACTIVE", "PENDING", "ACTIVE"
    ]
    
    with open("/app/output/transactions.csv", "r") as f:
        reader = csv.DictReader(f)
        records = list(reader)
    
    for i, (record, expected) in enumerate(zip(records, expected_statuses)):
        assert record["status"] == expected, \
            f"Record {i+1} status wrong: expected {expected}, got {record['status']}"


def test_trans_type_values_expanded():
    """Test that transaction type codes are expanded to full words"""
    expected_types = [
        "CREDIT", "DEBIT", "CREDIT", "DEBIT", "CREDIT",
        "DEBIT", "CREDIT", "DEBIT", "CREDIT", "DEBIT"
    ]
    
    with open("/app/output/transactions.csv", "r") as f:
        reader = csv.DictReader(f)
        records = list(reader)
    
    for i, (record, expected) in enumerate(zip(records, expected_types)):
        assert record["trans_type"] == expected, \
            f"Record {i+1} trans_type wrong: expected {expected}, got {record['trans_type']}"
