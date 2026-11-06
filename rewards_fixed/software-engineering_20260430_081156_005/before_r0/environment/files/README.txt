Transaction Data Migration Project
==================================

This directory contains transaction data exported from our legacy banking
mainframe system. The data needs to be converted to a modern CSV format
for import into our new transaction processing system.

Data Source
-----------
The transactions.dat file contains fixed-length binary records exported
from our IBM mainframe. All records are encoded using EBCDIC Code Page 037
(US/Canada), which is the standard encoding for our mainframe environment.

Record Layout
-------------
Each record is 41 bytes with the following structure:

  Offset  Length  Field           Format          Description
  ------  ------  --------------- --------------- ----------------------------
  0       1       System ID       Binary          Source system identifier
                                                0x01 = Primary mainframe
                                                0x02 = Secondary system
  1       8       Account ID      EBCDIC Text     Alphanumeric account number
  9       1       Trans Type      EBCDIC Text     'C' = Credit, 'D' = Debit
  10      5       Amount          COMP-3 Packed   Amount in cents (9 digits)
  15      25      Description     EBCDIC Text     Transaction description
  40      1       Status          EBCDIC Text     'A'=Active, 'P'=Pending, 'E'=Error

COMP-3 Packed Decimal Format
----------------------------
The Amount field uses IBM COMP-3 packed decimal format:
  - Each decimal digit is stored in a 4-bit nibble
  - The last nibble contains the sign: C=positive, D=negative, F=unsigned
  - For a 9-digit number, 5 bytes are used (9 digit nibbles + 1 sign nibble)

Example: +123456789 stored as: 12 34 56 78 9C

Output Format
-------------
Create a Python script at /app/convert.py that reads the binary data file
and outputs a CSV file at /app/output/transactions.csv with the following
columns:

  account_id, trans_type, amount, description, status

Where:
  - account_id: string (exactly as in the data)
  - trans_type: string ('CREDIT' or 'DEBIT' based on C/D code)
  - amount: decimal number with 2 decimal places (e.g., "123.45")
  - description: string (trimmed, no trailing spaces)
  - status: string ('ACTIVE', 'PENDING', or 'ERROR')

The CSV should have a header row and records in the same order as the
input file.

Notes
-----
- All records are encoded in EBCDIC Code Page 037 (cp037)
- The System ID field is included for reference but should not appear in output
- Amounts are stored in cents; convert to dollars with 2 decimal places
