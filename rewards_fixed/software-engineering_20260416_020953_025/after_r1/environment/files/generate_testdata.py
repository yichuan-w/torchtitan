#!/usr/bin/env python3
"""
Generate test binary files for binparse library testing.
Creates files with valid structure using byte-by-byte checksum (alignment-safe).
"""

import struct
import sys
import os

MAGIC = 0x42504152  # "BPAR"

def rotate_left_32(val, n=1):
    """Rotate a 32-bit value left by n bits."""
    return ((val << n) | (val >> (32 - n))) & 0xFFFFFFFF

def compute_checksum(data):
    """Compute the checksum used by the library's binary format."""
    sum_val = 0
    word_end = len(data) - (len(data) % 4)
    for offset in range(0, word_end, 4):
        word = int.from_bytes(data[offset:offset + 4], 'little')
        sum_val = (sum_val + word) & 0xFFFFFFFF
        sum_val = rotate_left_32(sum_val)
    for byte in data[word_end:]:
        sum_val = (sum_val + byte) & 0xFFFFFFFF
        sum_val = rotate_left_32(sum_val)
    return sum_val

def create_test_file(filename, num_records, record_data_list):
    """Create a binary test file with given records."""
    # Build header placeholder
    header_placeholder = struct.pack('<IIII', MAGIC, 1, num_records, 0)
    
    # Build records (type, length, offset, flags)
    records = b''
    data_section = b''
    data_offset = 16 + num_records * 16  # header + records
    
    for i, rec_data in enumerate(record_data_list):
        if rec_data:
            offset = data_offset + len(data_section)
            length = len(rec_data)
            # Compute data checksum using byte-by-byte method
            data_csum = compute_checksum(rec_data)
            records += struct.pack('<IIII', 1, length, offset, data_csum)
            data_section += rec_data
        else:
            records += struct.pack('<IIII', 0, 0, 0, 0)
    
    # Combine header + records for checksum
    header_and_records = header_placeholder + records
    checksum = compute_checksum(header_and_records)
    
    # Rebuild header with correct checksum
    header = struct.pack('<IIII', MAGIC, 1, num_records, checksum)
    
    # Write file
    with open(filename, 'wb') as f:
        f.write(header)
        f.write(records)
        f.write(data_section)
    
    print(f"Created {filename}: {num_records} records, {len(data_section)} bytes data")

def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    os.makedirs(output_dir, exist_ok=True)
    
    # Test file 1: Simple records
    create_test_file(
        os.path.join(output_dir, 'sample1.bin'),
        3,
        [
            b'AAAA' * 4,
            b'BBBB' * 8,
            b'CCCC' * 2,
        ]
    )
    
    # Test file 2: Records with variable sized data
    create_test_file(
        os.path.join(output_dir, 'sample2.bin'),
        5,
        [
            b'Hello',
            b'World' * 10,
            b'Test\x00Data',
            b'X' * 7,
            b'FinalRecord',
        ]
    )
    
    # Test file 3: Empty records
    create_test_file(
        os.path.join(output_dir, 'sample3.bin'),
        4,
        [b'', b'', b'', b'']
    )
    
    # Test file 4: Large records
    create_test_file(
        os.path.join(output_dir, 'sample4.bin'),
        2,
        [
            b'A' * 1000,
            b'B' * 2000,
        ]
    )
    
    # Test file 5: Many small records
    create_test_file(
        os.path.join(output_dir, 'sample5.bin'),
        10,
        [chr(ord('a') + i).encode() * 8 for i in range(10)]
    )
    
    print(f"\nGenerated 5 test files in {output_dir}")

if __name__ == '__main__':
    main()
