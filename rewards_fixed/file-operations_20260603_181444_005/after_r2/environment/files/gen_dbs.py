#!/usr/bin/env python3
"""Generate the corrupted SQLite databases for the task environment.

Creates two databases with complementary corruption:
  - data.db (page_size=4096): magic string zeroed (bytes 0-15),
    file-format versions corrupted (bytes 18-19 set to 0xFFFF),
    and page size zeroed (bytes 16-17).
  - reference.db (page_size=1024): fully intact header, minimal data.
    Different page size from data.db so the solver cannot blindly
    copy the entire header.
"""

import sqlite3
import os
import sys

if len(sys.argv) > 1:
    OUT_DIR = sys.argv[1]
else:
    OUT_DIR = "/tmp"
DATA_DB = f"{OUT_DIR}/data.db"
REFERENCE_DB = f"{OUT_DIR}/reference.db"


def create_data_db(path):
    """Create a populated database with default page size (4096)."""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            order_date TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            sku TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            stock INTEGER DEFAULT 0
        )
    """)
    
    c.executemany(
        "INSERT INTO users (id, name, email, created_at) VALUES (?, ?, ?, ?)",
        [
            (1, "Alice Johnson", "alice@example.com", "2024-01-15 09:30:00"),
            (2, "Bob Smith", "bob@example.com", "2024-02-20 14:15:00"),
            (3, "Charlie Brown", "charlie@example.com", "2024-03-10 11:00:00"),
            (4, "Diana Prince", "diana@example.com", "2024-04-05 16:45:00"),
            (5, "Evan Wright", "evan@example.com", "2024-05-12 08:20:00"),
        ]
    )
    c.executemany(
        "INSERT INTO products (id, sku, name, category, stock) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "WDG-001", "Blue Widget", "Widgets", 150),
            (2, "WDG-002", "Red Widget", "Widgets", 200),
            (3, "GZM-001", "Standard Gizmo", "Gizmos", 75),
            (4, "GZM-002", "Deluxe Gizmo", "Gizmos", 30),
            (5, "DBT-001", "Thingamabob", "Doohickeys", 500),
            (6, "DBT-002", "Whatchamacallit", "Doohickeys", 120),
        ]
    )
    c.executemany(
        "INSERT INTO orders (id, user_id, product, quantity, price, order_date) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, "Blue Widget", 5, 9.99, "2024-06-01 10:00:00"),
            (2, 1, "Standard Gizmo", 2, 24.99, "2024-06-02 14:30:00"),
            (3, 2, "Red Widget", 10, 9.99, "2024-06-03 09:15:00"),
            (4, 3, "Deluxe Gizmo", 1, 49.99, "2024-06-04 11:00:00"),
            (5, 4, "Thingamabob", 3, 5.99, "2024-06-05 16:45:00"),
            (6, 5, "Blue Widget", 8, 9.99, "2024-06-06 08:30:00"),
            (7, 5, "Whatchamacallit", 2, 15.99, "2024-06-07 13:00:00"),
            (8, 2, "Standard Gizmo", 4, 24.99, "2024-06-08 10:30:00"),
            (9, 3, "Thingamabob", 12, 5.99, "2024-06-09 15:00:00"),
            (10, 4, "Red Widget", 6, 9.99, "2024-06-10 09:00:00"),
        ]
    )
    conn.commit()
    conn.close()


def create_reference_db(path):
    """Create a near-empty database with page_size=1024 and same schema."""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    # Set page size and vacuum to apply it (must be done before creating objects)
    conn.execute("PRAGMA page_size=1024")
    conn.execute("VACUUM")
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            order_date TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            sku TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            stock INTEGER DEFAULT 0
        )
    """)
    conn.execute("INSERT INTO users (id, name, email) VALUES (1, 'Reference', 'ref@db.io')")
    conn.commit()
    conn.close()


def corrupt_data_db(path):
    """Corrupt data.db header:
       - bytes 0-15: magic string zeroed
       - bytes 16-17: page size zeroed (was 0x1000 = 4096)
       - bytes 18-19: file-format versions set to 0xFFFF

       The solver can get magic and versions from reference.db, but page size
       must be determined independently because reference.db uses 1024.
    """
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(b'\x00' * 16)       # destroy magic string
        f.seek(16)
        f.write(b'\x00\x00')        # destroy page size
        f.seek(18)
        f.write(b'\xff\xff')        # corrupt format versions


def main():
    print("Creating data.db (page_size=4096, populated)...")
    create_data_db(DATA_DB)

    print("Creating reference.db (page_size=1024, near-empty)...")
    create_reference_db(REFERENCE_DB)

    print("Corrupting data.db header...")
    corrupt_data_db(DATA_DB)

    print("Done!")

    # Verify corruption
    for path, name in [(DATA_DB, "data.db"), (REFERENCE_DB, "reference.db")]:
        try:
            conn = sqlite3.connect(path)
            conn.execute("SELECT 1")
            print(f"WARNING: {name} is still openable!")
            conn.close()
        except sqlite3.DatabaseError as e:
            print(f"OK: {name} correctly corrupted: {e}")

    # Show header comparison
    print("\n=== data.db header (first 32 bytes) ===")
    with open(DATA_DB, "rb") as f:
        data = f.read(32)
    for i in range(0, 32, 16):
        hex_str = " ".join(f"{b:02x}" for b in data[i:i+16])
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
        print(f"  {i:04x}: {hex_str:<48s} {ascii_str}")

    print("\n=== reference.db header (first 32 bytes) ===")
    with open(REFERENCE_DB, "rb") as f:
        data = f.read(32)
    for i in range(0, 32, 16):
        hex_str = " ".join(f"{b:02x}" for b in data[i:i+16])
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
        print(f"  {i:04x}: {hex_str:<48s} {ascii_str}")


if __name__ == "__main__":
    main()
