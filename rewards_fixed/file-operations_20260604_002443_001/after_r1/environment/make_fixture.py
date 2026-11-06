from pathlib import Path
import struct

archive_path = Path('/app/data.bin')
raw = archive_path.read_bytes()
columns = [0x20, 0x40, 0x80, 0x01, 0x02, 0x04, 0x08, 0x9E]

def matrix(value):
    result = 0
    for bit, column in enumerate(columns):
        if value >> bit & 1:
            result ^= column
    return result

forward = [matrix(value) ^ 0xAA for value in range(256)]
inverse = [0] * 256
for plain, encoded in enumerate(forward):
    inverse[encoded] = plain
plain_archive = bytes(inverse[value] for value in raw)
assert plain_archive[:4] == b'ARC\x01'

plain_archive = bytearray(plain_archive)
boundaries = [216, 337, 525, 735, 940, len(plain_archive)]
for entry_number, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
    struct.pack_into('<II', plain_archive, 16 + entry_number * 40 + 32, start, end - start)

def rotate_left(value, count):
    return ((value << count) | (value >> (8 - count))) & 0xFF

archive_path.write_bytes(bytes(rotate_left(value, 3) ^ 0xA5 for value in plain_archive))
