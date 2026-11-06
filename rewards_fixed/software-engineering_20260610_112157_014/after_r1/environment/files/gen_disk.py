#!/usr/bin/env python3
"""Generate a FAT16 disk image with deleted files across root and subdirectories."""

import struct, subprocess, os, sys

output_path = sys.argv[1] if len(sys.argv) > 1 else '/app/disk.img'

# File contents (all names fit in 8.3 format: <=8 chars name, <=3 chars ext)
file_contents = {
    "README.TXT": b"This is the readme file.\nIt contains important information about the project.\nThis file is NOT deleted.\n",
    "NOTES.TXT": b"Project Notes:\n- Fix the login bug\n- Update documentation\n- Release by Friday\nThese notes are confidential.\n",
    "CONFIG.DAT": bytes([0x01,0x00,0x02,0x00,0xFF,0xFE,0xFD,0xFC,
                         0x48,0x65,0x6C,0x6C,0x6F,0x00,0x00,0x00,
                         0xDE,0xAD,0xBE,0xEF,0xCA,0xFE,0xBA,0xBE]),
    "DATA.BIN": bytes(range(64)),
    "MAIN.C": b"// main.c - Project source file\n#include <stdio.h>\n\nint main() {\n    printf(\"Hello World\\n\");\n    return 0;\n}\n",
    "SECRET.TXT": b"THE SECRET MESSAGE IS: fat16_recovery_2024\nDo not share this with anyone.\n",
    "LOG.TXT": b"".join(f"Line {i:04d}: This is some data to fill up the file and make it span multiple clusters.\n".encode() for i in range(100)),
    "BUILD_SH.TXT": b"#!/bin/bash\ngcc -o program main.c\n./program\n",
    "NOTEBACK.TXT": b"This is a backup of old notes.\nVersion 1.0 - Archived.\n",
    "READ_DEV.TXT": b"Developer readme - internal use only.\nDo not distribute.\n",
}

# Files to create: (path_in_image, source_filename)
files_to_create = [
    # Root directory
    ("README.TXT", "README.TXT"),
    ("NOTES.TXT", "NOTES.TXT"),
    ("CONFIG.DAT", "CONFIG.DAT"),
    ("DATA.BIN", "DATA.BIN"),
    # PROJECT subdirectory
    ("PROJECT/MAIN.C", "MAIN.C"),
    ("PROJECT/SECRET.TXT", "SECRET.TXT"),
    ("PROJECT/BUILD_SH.TXT", "BUILD_SH.TXT"),
    # PROJECT/BUILD nested subdirectory  
    ("PROJECT/BUILD/LOG.TXT", "LOG.TXT"),
    # PROJECT/DOCS subdirectory with a deleted file
    ("PROJECT/DOCS/READ_DEV.TXT", "READ_DEV.TXT"),
    # BACKUP subdirectory 
    ("BACKUP/NOTEBACK.TXT", "NOTEBACK.TXT"),
]

files_to_delete = [
    "NOTES.TXT",                    # in root
    "CONFIG.DAT",                   # in root
    "PROJECT/SECRET.TXT",           # in PROJECT/
    "PROJECT/BUILD/LOG.TXT",        # in PROJECT/BUILD/
    "PROJECT/DOCS/READ_DEV.TXT",    # in PROJECT/DOCS/
    "BACKUP/NOTEBACK.TXT",          # in BACKUP/
]

# Write temp files
for _, src_name in files_to_create:
    with open(f"/tmp/{src_name}", "wb") as f:
        f.write(file_contents[src_name])

# Create image
subprocess.run(["dd", "if=/dev/zero", "of=" + output_path, "bs=1M", "count=16"],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(["mkfs.fat", "-F", "16", output_path],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Create directories
for d in ["PROJECT", "PROJECT/BUILD", "PROJECT/DOCS", "BACKUP"]:
    subprocess.run(["mmd", "-i", output_path, f"::{d}"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Copy files
for dest, src_name in files_to_create:
    subprocess.run(["mcopy", "-i", output_path, f"/tmp/{src_name}", f"::{dest}"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.remove(f"/tmp/{src_name}")

# Now parse the image and simulate deletion
with open(output_path, "rb") as f:
    data = bytearray(f.read())

bps = struct.unpack_from("<H", data, 11)[0]
spc = data[13]
rsvd = struct.unpack_from("<H", data, 14)[0]
nfats = data[16]
root_ents = struct.unpack_from("<H", data, 17)[0]
spf = struct.unpack_from("<H", data, 22)[0]

fat_start = rsvd * bps
root_start = fat_start + nfats * spf * bps
root_size = root_ents * 32
data_start = root_start + root_size
cluster_size = bps * spc

def read_fat(c):
    return struct.unpack_from("<H", data, fat_start + c * 2)[0]

def write_fat(c, v):
    struct.pack_into("<H", data, fat_start + c * 2, v)
    struct.pack_into("<H", data, fat_start + spf * bps + c * 2, v)

def cluster_offset(c):
    return data_start + (c - 2) * cluster_size

def entry_name(entry):
    """Get the 8.3 name from a directory entry."""
    name_raw = entry[0:11]
    ns = name_raw[0:8].rstrip(b" ").decode("ascii", errors="replace")
    ex = name_raw[8:11].rstrip(b" ").decode("ascii", errors="replace")
    return f"{ns}.{ex}" if ex else ns

def read_dir_entries(offset, size):
    """Read directory entries from a specific offset in the image."""
    entries = []
    for off in range(0, size, 32):
        entry = data[offset + off:offset + off + 32]
        if entry[0] == 0x00:
            break
        if entry[0] == 0xE5:
            continue  # Skip already deleted
        entries.append(entry)
    return entries

def read_cluster_chain(start_cluster):
    """Read all clusters in a chain and return as bytes."""
    result = b""
    c = start_cluster
    while True:
        result += data[cluster_offset(c):cluster_offset(c) + cluster_size]
        fv = read_fat(c)
        if fv >= 0xFFF8:
            break
        c = fv
    return result

def find_and_delete_in_dir(dir_offset, dir_size, target_name_upper):
    """Find a file by name in directory entries and mark it deleted + zero FAT."""
    for off in range(0, dir_size, 32):
        entry_start = dir_offset + off
        entry = data[entry_start:entry_start + 32]
        if entry[0] == 0x00:
            break
        if entry[0] == 0xE5:
            continue
        if (entry[11] & 0x10) != 0:  # Directory attribute
            continue
        if entry_name(entry).upper() == target_name_upper:
            data[entry_start] = 0xE5
            c = struct.unpack_from("<H", entry, 26)[0]
            while True:
                fv = read_fat(c)
                write_fat(c, 0x0000)
                if fv >= 0xFFF8:
                    break
                c = fv
            return True
    return False

def find_dir_cluster(dir_offset, dir_size, target_name_upper):
    """Find a subdirectory's first cluster by name."""
    for off in range(0, dir_size, 32):
        entry_start = dir_offset + off
        entry = data[entry_start:entry_start + 32]
        if entry[0] == 0x00:
            break
        if entry[0] == 0xE5:
            continue
        if (entry[11] & 0x10) == 0:  # Not a directory
            continue
        if entry_name(entry).upper() == target_name_upper:
            return struct.unpack_from("<H", entry, 26)[0]
    return None

# Recursively delete files in directories
def process_directory(dir_offset, dir_size, path_prefix, dir_name):
    """Process a directory: delete files in it and recurse into subdirs."""
    subdirs = []
    
    # First pass: find all subdirectories
    for off in range(0, dir_size, 32):
        entry_start = dir_offset + off
        entry = data[entry_start:entry_start + 32]
        if entry[0] == 0x00:
            break
        if entry[0] == 0xE5:
            continue
        if (entry[11] & 0x10) != 0 and entry_name(entry) not in ('.', '..'):
            subdirs.append((entry_name(entry), struct.unpack_from('<H', entry, 26)[0]))
    
    # Delete files in this directory
    for fname in files_to_delete:
        parts = fname.split("/")
        if len(parts) == len(path_prefix) + 1:
            # This file should be in this directory
            target = parts[-1]
            if path_prefix == parts[:-1]:
                if find_and_delete_in_dir(dir_offset, dir_size, target.upper()):
                    print(f"Deleted {fname}")
    
    # Recurse into subdirectories
    for sub_name, sub_cluster in subdirs:
        sub_offset = cluster_offset(sub_cluster)
        sub_data = read_cluster_chain(sub_cluster)
        sub_size = len(sub_data)
        process_directory(sub_offset, sub_size, path_prefix + [sub_name], sub_name)

# Process root directory (recursively finds and deletes files in all subdirs)
process_directory(root_start, root_size, [], "ROOT")

# Corrupt the boot sector: zero out the "sectors per FAT" and "total sectors" fields
# This forces the solver to determine filesystem layout from other clues
# (e.g., the FAT signature bytes, backup boot sector, or manual calculation)
# Zero out bytes 22-23 (sectors per FAT) and 19-20 (total sectors)
data[22] = 0
data[23] = 0
# Also corrupt bytes 17-18 (root entries) to make it harder
# Actually, let's keep root entries intact so the task remains solvable

with open(output_path, "wb") as f:
    f.write(data)

print(f"\nImage written to {output_path}")
print("Boot sector corrupted: sectors_per_fat zeroed")