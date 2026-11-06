#!/usr/bin/env python3
"""
Generate test log files with various encoding issues for the encoding normalizer task.
This script creates a set of log files with different encodings that simulate a legacy
system migration export.
"""

import os
import random

# Seed for reproducibility
random.seed(42)

# Output directory
OUTPUT_DIR = "/app/logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Base log entries with special characters
LOG_TEMPLATES = [
    "[{timestamp}] INFO  - User {user} logged in from {ip}",
    "[{timestamp}] WARN  - Connection timeout for {user}@{host}",
    "[{timestamp}] ERROR - Database query failed: {query}",
    "[{timestamp}] INFO  - Processing order #{order_id} for {customer}",
    "[{timestamp}] DEBUG - API response: {response}",
    "[{timestamp}] INFO  - File uploaded: {filename}",
    "[{timestamp}] WARN  - Rate limit approaching for client {client}",
    "[{timestamp}] ERROR - Authentication failed for user '{user}'",
]

# Data with special characters that will cause encoding issues
UTF8_DATA = {
    "users": ["François", "José García", "Müller", "Sørensen", "Björk", "Łukasz", "Zoë", "Chloé"],
    "customers": ["Café de Paris", "Zur Brücke", "El Niño", "Mjølnir AS", "Österreich GmbH"],
    "filenames": ["rapport_été.pdf", "überblick.doc", "niño.jpg", "naïve.txt", "résumé.docx"],
    "queries": ["SELECT * FROM users WHERE name='Søren'", "UPDATE orders SET status='en_cours'"],
    "responses": ['{"status":"réussi","count":42}', '{"error":"échec de connexion"}'],
    "hosts": ["srv-paris.example.com", "db-berlin.internal", "cache-oslo.local"],
}

LATIN1_DATA = {
    "users": ["François", "José", "Renée", "André", "Björk"],
    "customers": ["Café du Coin", "Brücke AG", "El Niño SA"],
    "filenames": ["été_2024.pdf", "über.doc", "niño_data.csv"],
}

WINDOWS1252_DATA = {
    "users": ["François", "Zoë", "Chloé"],
    "customers": ["Café London", "Brötchen GmbH"],
    # Windows-1252 has specific characters like smart quotes
    "messages": ["User's session expired", "Don't retry"],
}

# Mojibake patterns - text that was UTF-8, incorrectly decoded as Latin-1, then re-encoded as UTF-8
MOJIBAKE_ORIGINALS = [
    ("User café authenticated", "User cafÃ© authenticated"),
    ("Order from Müller GmbH", "Order from MÃ¼ller GmbH"),
    ("File naïve_approach.py uploaded", "File naÃ¯ve_approach.py uploaded"),
    ("Server réponse: succès", "Server rÃ©ponse: succÃ¨s"),
    ("Client Zürich connected", "Client ZÃ¼rich connected"),
    ("Error: délai expiré", "Error: dÃ©lai expirÃ©"),
    ("Warning: température élevée", "Warning: tempÃ©rature Ã©levÃ©e"),
    ("Processing förstagång data", "Processing fÃ¶rstagÃ¥ng data"),
]


def generate_timestamp(base_hour: int, minute: int, second: int) -> str:
    """Generate a timestamp string."""
    return f"2024-03-15 {base_hour:02d}:{minute:02d}:{second:02d}"


def generate_ip() -> str:
    """Generate a fake IP address."""
    return f"192.168.{random.randint(1, 254)}.{random.randint(1, 254)}"


def generate_order_id() -> int:
    """Generate a fake order ID."""
    return random.randint(10000, 99999)


def generate_client_id() -> str:
    """Generate a fake client ID."""
    return f"CLI-{random.randint(1000, 9999)}"


def generate_utf8_logs(count: int) -> bytes:
    """Generate logs with proper UTF-8 encoding (no BOM)."""
    lines = []
    for i in range(count):
        template = random.choice(LOG_TEMPLATES)
        ts = generate_timestamp(10 + i // 60, i % 60, random.randint(0, 59))
        line = template.format(
            timestamp=ts,
            user=random.choice(UTF8_DATA["users"]),
            ip=generate_ip(),
            host=random.choice(UTF8_DATA["hosts"]),
            query=random.choice(UTF8_DATA["queries"]),
            order_id=generate_order_id(),
            customer=random.choice(UTF8_DATA["customers"]),
            response=random.choice(UTF8_DATA["responses"]),
            filename=random.choice(UTF8_DATA["filenames"]),
            client=generate_client_id(),
        )
        lines.append(line)
    return "\n".join(lines).encode("utf-8")


def generate_utf8_bom_logs(count: int) -> bytes:
    """Generate logs with UTF-8 encoding and BOM marker."""
    content = generate_utf8_logs(count)
    # Add BOM at the beginning
    return b'\xef\xbb\xbf' + content


def generate_latin1_logs(count: int) -> bytes:
    """Generate logs with Latin-1 (ISO-8859-1) encoding."""
    lines = []
    for i in range(count):
        template = random.choice(LOG_TEMPLATES)
        ts = generate_timestamp(14 + i // 60, i % 60, random.randint(0, 59))
        line = template.format(
            timestamp=ts,
            user=random.choice(LATIN1_DATA["users"]),
            ip=generate_ip(),
            host="legacy-server.internal",
            query="SELECT * FROM old_table",
            order_id=generate_order_id(),
            customer=random.choice(LATIN1_DATA["customers"]),
            response='{"legacy":true}',
            filename=random.choice(LATIN1_DATA["filenames"]),
            client=generate_client_id(),
        )
        lines.append(line)
    # Latin-1 encoding - characters like é, ö, ü are single bytes
    return "\n".join(lines).encode("latin-1")


def generate_windows1252_logs(count: int) -> bytes:
    """Generate logs with Windows-1252 encoding."""
    lines = []
    for i in range(count):
        template = random.choice(LOG_TEMPLATES)
        ts = generate_timestamp(18 + i // 60, i % 60, random.randint(0, 59))
        line = template.format(
            timestamp=ts,
            user=random.choice(WINDOWS1252_DATA["users"]),
            ip=generate_ip(),
            host="win-server.internal",
            query="EXEC sp_get_data",
            order_id=generate_order_id(),
            customer=random.choice(WINDOWS1252_DATA["customers"]),
            response='{"windows":true}',
            filename="data_export.csv",
            client=generate_client_id(),
        )
        lines.append(line)
    # Windows-1252 is similar to Latin-1 but has some differences
    return "\n".join(lines).encode("windows-1252")


def generate_triple_encoded_logs(count: int) -> bytes:
    """Generate logs with triple-encoded mojibake (even more corrupted)."""
    lines = []
    for i in range(count):
        original, _ = random.choice(MOJIBAKE_ORIGINALS)
        # Triple encoding: UTF-8 -> Latin-1 decode -> UTF-8 encode -> Latin-1 decode -> UTF-8 encode
        utf8_bytes = original.encode('utf-8')
        # First mis-decode
        mojibake1 = utf8_bytes.decode('latin-1')
        # Re-encode and mis-decode again
        mojibake2 = mojibake1.encode('utf-8').decode('latin-1')
        # Final encode
        final = mojibake2.encode('utf-8')
        
        ts = generate_timestamp(4 + i // 60, i % 60, random.randint(0, 59))
        line = f"[{ts}] WARN  - Legacy backup: {final}"
        lines.append(line.encode('utf-8'))
    return b"\n".join(lines)


def generate_shiftjis_logs(count: int) -> bytes:
    """Generate logs with Shift-JIS encoding (Japanese)."""
    japanese_data = [
        ("ユーザー", "User"),  # User
        ("ログイン", "login"),  # login
        ("エラー", "error"),  # error
        ("処理中", "processing"),  # processing
        ("完了", "complete"),  # complete
    ]
    lines = []
    for i in range(count):
        ts = generate_timestamp(20 + i // 60, i % 60, random.randint(0, 59))
        jp, en = random.choice(japanese_data)
        line = f"[{ts}] INFO  - {jp} ({en}): session_{generate_client_id()}"
        lines.append(line)
    return "\n".join(lines).encode("shift-jis")


def generate_utf16_le_logs(count: int) -> bytes:
    """Generate logs with UTF-16 LE encoding (with BOM)."""
    lines = []
    for i in range(count):
        template = random.choice(LOG_TEMPLATES)
        ts = generate_timestamp(12 + i // 60, i % 60, random.randint(0, 59))
        line = template.format(
            timestamp=ts,
            user="Владимир",  # Russian name
            ip=generate_ip(),
            host="ru-server.internal",
            query="SELECT данные FROM таблица",
            order_id=generate_order_id(),
            customer="Компания ООО",  # Russian company
            response='{"status":"готово"}',
            filename="отчёт.csv",
            client=generate_client_id(),
        )
        lines.append(line)
    content = "\n".join(lines)
    # UTF-16 LE with BOM
    return content.encode("utf-16-le")


def generate_iso8859_15_logs(count: int) -> bytes:
    """Generate logs with ISO-8859-15 encoding (Euro symbol support)."""
    euro_data = [
        "Prix: 50€",
        "Kosten: 100€",
        "Prezzo: 75€",
        "Precio: 30€",
    ]
    lines = []
    for i in range(count):
        ts = generate_timestamp(16 + i // 60, i % 60, random.randint(0, 59))
        price = random.choice(euro_data)
        line = f"[{ts}] INFO  - Order #{generate_order_id()}: {price}"
        lines.append(line)
    return "\n".join(lines).encode("iso-8859-15")


def generate_mojibake_logs(count: int) -> bytes:
    """Generate logs with double-encoded mojibake (UTF-8 text incorrectly decoded as Latin-1, then re-encoded)."""
    lines = []
    for i in range(count):
        _, mojibake = random.choice(MOJIBAKE_ORIGINALS)
        ts = generate_timestamp(22 + i // 60, i % 60, random.randint(0, 59))
        line = f"[{ts}] INFO  - {mojibake}"
        lines.append(line)
    # The mojibake strings are already "corrupted" - we just encode them as UTF-8
    return "\n".join(lines).encode("utf-8")


def generate_mixed_encoding_logs(count: int) -> bytes:
    """Generate logs where individual lines have different encodings (simulating appended logs from different sources)."""
    lines_bytes = []
    
    # Mix of encodings within the same file
    for i in range(count):
        ts = generate_timestamp(2 + i // 60, i % 60, random.randint(0, 59))
        
        if i % 4 == 0:
            # UTF-8 line
            line = f"[{ts}] INFO  - UTF-8 user: {random.choice(UTF8_DATA['users'])}"
            lines_bytes.append(line.encode("utf-8"))
        elif i % 4 == 1:
            # Latin-1 line
            line = f"[{ts}] INFO  - Latin-1 user: {random.choice(LATIN1_DATA['users'])}"
            lines_bytes.append(line.encode("latin-1"))
        elif i % 4 == 2:
            # Windows-1252 line
            line = f"[{ts}] INFO  - Windows user: {random.choice(WINDOWS1252_DATA['users'])}"
            lines_bytes.append(line.encode("windows-1252"))
        else:
            # Mojibake line
            _, mojibake = random.choice(MOJIBAKE_ORIGINALS)
            line = f"[{ts}] INFO  - Corrupted: {mojibake}"
            lines_bytes.append(line.encode("utf-8"))
    
    # Join with newline bytes - this creates a truly mixed encoding file
    return b"\n".join(lines_bytes)


def generate_incomplete_multibyte_logs(count: int) -> bytes:
    """Generate logs with incomplete multi-byte UTF-8 sequences (truncated at byte boundaries)."""
    lines = []
    for i in range(count):
        ts = generate_timestamp(6 + i // 60, i % 60, random.randint(0, 59))
        # Use ASCII-only for most lines to avoid issues
        line = f"[{ts}] INFO  - ASCII safe log entry number {i}"
        lines.append(line)
    
    content = "\n".join(lines).encode("utf-8")
    
    # Add a line with UTF-8 characters, then truncate it mid-character
    last_line = f"\n[{generate_timestamp(8, 0, 0)}] INFO  - Truncated: café"
    last_line_bytes = last_line.encode("utf-8")
    # Truncate to remove the last byte (which is part of 'é')
    truncated = last_line_bytes[:-1]
    
    return content + truncated


def main():
    """Generate all test log files."""
    
    # File 1: Clean UTF-8 (no BOM) - baseline
    with open(os.path.join(OUTPUT_DIR, "app_2024-03-15.log"), "wb") as f:
        f.write(generate_utf8_logs(50))
    
    # File 2: UTF-8 with BOM (from Windows export)
    with open(os.path.join(OUTPUT_DIR, "app_2024-03-15_win.log"), "wb") as f:
        f.write(generate_utf8_bom_logs(40))
    
    # File 3: Latin-1 encoded (from legacy Unix system)
    with open(os.path.join(OUTPUT_DIR, "legacy_export.log"), "wb") as f:
        f.write(generate_latin1_logs(35))
    
    # File 4: Windows-1252 encoded (from Windows application)
    with open(os.path.join(OUTPUT_DIR, "windows_app.log"), "wb") as f:
        f.write(generate_windows1252_logs(30))
    
    # File 5: Mojibake (double-encoded UTF-8)
    with open(os.path.join(OUTPUT_DIR, "migrated_data.log"), "wb") as f:
        f.write(generate_mojibake_logs(45))
    
    # File 6: Mixed encodings within the file (appended logs)
    with open(os.path.join(OUTPUT_DIR, "consolidated.log"), "wb") as f:
        f.write(generate_mixed_encoding_logs(60))
    
    # File 7: Contains incomplete multi-byte sequence
    with open(os.path.join(OUTPUT_DIR, "partial.log"), "wb") as f:
        f.write(generate_incomplete_multibyte_logs(25))
    
    # File 8: Triple-encoded mojibake (more complex corruption)
    with open(os.path.join(OUTPUT_DIR, "archive_corrupted.log"), "wb") as f:
        f.write(generate_triple_encoded_logs(20))
    
    # File 9: Shift-JIS encoded (Japanese system)
    with open(os.path.join(OUTPUT_DIR, "tokyo_server.log"), "wb") as f:
        f.write(generate_shiftjis_logs(25))
    
    # File 10: ISO-8859-15 encoded (Euro symbol support)
    with open(os.path.join(OUTPUT_DIR, "euro_orders.log"), "wb") as f:
        f.write(generate_iso8859_15_logs(30))
    
    print(f"Generated 10 log files in {OUTPUT_DIR}/")
    
    # Print file info
    for fname in sorted(os.listdir(OUTPUT_DIR)):
        fpath = os.path.join(OUTPUT_DIR, fname)
        size = os.path.getsize(fpath)
        print(f"  {fname}: {size} bytes")


if __name__ == "__main__":
    main()
