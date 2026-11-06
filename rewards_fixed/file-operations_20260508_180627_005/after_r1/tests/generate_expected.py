#!/usr/bin/env python3
"""
Generate the expected normalized output for testing purposes.
This script creates what the solver's output should look like after
proper encoding normalization.

This file is used ONLY during testing and is NOT visible to the solver.
"""

import os
import random

# Seed for reproducibility - must match generate_logs.py
random.seed(42)

OUTPUT_DIR = "/app/logs"
EXPECTED_FILE = "/app/expected_normalized.txt"


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


# Data - must match generate_logs.py exactly
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
}

# Original strings (before mojibake corruption)
MOJIBAKE_ORIGINALS = [
    "User café authenticated",
    "Order from Müller GmbH",
    "File naïve_approach.py uploaded",
    "Server réponse: succès",
    "Client Zürich connected",
    "Error: délai expiré",
    "Warning: température élevée",
    "Processing förstagång data",
]

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


def generate_expected_utf8_logs(count: int) -> list:
    """Generate expected UTF-8 logs (from app_2024-03-15.log and app_2024-03-15_win.log)."""
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
    return lines


def generate_expected_latin1_logs(count: int) -> list:
    """Generate expected logs from Latin-1 source (properly decoded to Unicode)."""
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
    return lines


def generate_expected_windows1252_logs(count: int) -> list:
    """Generate expected logs from Windows-1252 source (properly decoded to Unicode)."""
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
    return lines


def generate_expected_mojibake_logs(count: int) -> list:
    """Generate expected logs with mojibake fixed."""
    lines = []
    for i in range(count):
        original = random.choice(MOJIBAKE_ORIGINALS)
        ts = generate_timestamp(22 + i // 60, i % 60, random.randint(0, 59))
        line = f"[{ts}] INFO  - {original}"
        lines.append(line)
    return lines


def generate_expected_mixed_logs(count: int) -> list:
    """Generate expected logs from mixed encoding source (all properly decoded)."""
    lines = []
    for i in range(count):
        ts = generate_timestamp(2 + i // 60, i % 60, random.randint(0, 59))
        
        if i % 4 == 0:
            line = f"[{ts}] INFO  - UTF-8 user: {random.choice(UTF8_DATA['users'])}"
        elif i % 4 == 1:
            line = f"[{ts}] INFO  - Latin-1 user: {random.choice(LATIN1_DATA['users'])}"
        elif i % 4 == 2:
            line = f"[{ts}] INFO  - Windows user: {random.choice(WINDOWS1252_DATA['users'])}"
        else:
            original = random.choice(MOJIBAKE_ORIGINALS)
            line = f"[{ts}] INFO  - Corrupted: {original}"
        lines.append(line)
    return lines


def generate_expected_incomplete_logs(count: int) -> list:
    """Generate expected logs from incomplete multibyte source (truncated line handled)."""
    lines = []
    for i in range(count):
        ts = generate_timestamp(6 + i // 60, i % 60, random.randint(0, 59))
        line = f"[{ts}] INFO  - ASCII safe log entry number {i}"
        lines.append(line)
    # The truncated line should be handled gracefully - either skipped or partially recovered
    # We'll accept the file as valid if it contains the complete lines
    return lines


def main():
    """Generate the expected normalized output."""
    all_lines = []
    
    # File 1: Clean UTF-8 (50 lines)
    all_lines.extend(generate_expected_utf8_logs(50))
    
    # File 2: UTF-8 with BOM (40 lines) - BOM stripped
    all_lines.extend(generate_expected_utf8_logs(40))
    
    # File 3: Latin-1 (35 lines)
    all_lines.extend(generate_expected_latin1_logs(35))
    
    # File 4: Windows-1252 (30 lines)
    all_lines.extend(generate_expected_windows1252_logs(30))
    
    # File 5: Mojibake fixed (45 lines)
    all_lines.extend(generate_expected_mojibake_logs(45))
    
    # File 6: Mixed encodings (60 lines)
    all_lines.extend(generate_expected_mixed_logs(60))
    
    # File 7: Incomplete multibyte (25 complete lines, 1 truncated)
    all_lines.extend(generate_expected_incomplete_logs(25))
    
    # Write expected output
    with open(EXPECTED_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines))
        f.write("\n")  # Ensure trailing newline
    
    print(f"Generated expected output: {EXPECTED_FILE}")
    print(f"Total lines: {len(all_lines)}")


if __name__ == "__main__":
    main()
