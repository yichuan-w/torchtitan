import os
import subprocess
import sys
from pathlib import Path

# Paths
OUTPUT_FILE = Path("/app/normalized_output.txt")
LOGS_DIR = Path("/app/logs")
EXPECTED_FILE = Path("/app/expected_normalized.txt")


def run_normalize_script():
    """Run the normalize_encoding.py script."""
    script_path = Path("/app/normalize_encoding.py")
    if not script_path.exists():
        return None, "Script not found at /app/normalize_encoding.py"
    
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/app"
    )
    return result, None


def test_normalize_script_exists():
    """Verify that the normalization script was created."""
    script_path = Path("/app/normalize_encoding.py")
    assert script_path.exists(), f"Script not found at {script_path}"
    assert script_path.stat().st_size > 0, "Script file is empty"


def test_output_file_exists():
    """Verify that the output file was created."""
    assert OUTPUT_FILE.exists(), f"Output file not found at {OUTPUT_FILE}"
    assert OUTPUT_FILE.stat().st_size > 0, "Output file is empty"


def test_output_is_valid_utf8():
    """Verify that the output file is valid UTF-8."""
    try:
        content = OUTPUT_FILE.read_text(encoding="utf-8")
        assert len(content) > 0, "Output file has no content"
    except UnicodeDecodeError as e:
        assert False, f"Output file is not valid UTF-8: {e}"


def test_no_bom_in_output():
    """Verify that the output file has no BOM marker."""
    raw_bytes = OUTPUT_FILE.read_bytes()
    # UTF-8 BOM
    assert not raw_bytes.startswith(b'\xef\xbb\xbf'), "Output file contains UTF-8 BOM"
    # UTF-16 LE BOM
    assert not raw_bytes.startswith(b'\xff\xfe'), "Output file contains UTF-16 LE BOM"
    # UTF-16 BE BOM
    assert not raw_bytes.startswith(b'\xfe\xff'), "Output file contains UTF-16 BE BOM"


def test_unix_line_endings():
    """Verify that the output uses Unix line endings (LF only, no CRLF)."""
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    assert "\r\n" not in content, "Output file contains Windows line endings (CRLF)"


def test_correct_line_count():
    """Verify that the output has approximately the expected number of lines."""
    # Total expected: 50 + 40 + 35 + 30 + 45 + 60 + 25 + 20 + 25 + 30 = 360 lines
    # Allow some tolerance for truncated lines
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    lines = [l for l in content.split("\n") if l.strip()]
    assert len(lines) >= 350, f"Expected at least 350 lines, got {len(lines)}"
    assert len(lines) <= 370, f"Expected at most 370 lines, got {len(lines)}"


def test_mojibake_fixed():
    """Verify that mojibake patterns have been fixed."""
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    
    # These patterns indicate mojibake was NOT fixed
    mojibake_patterns = [
        "cafÃ©",      # café incorrectly encoded
        "MÃ¼ller",    # Müller incorrectly encoded
        "naÃ¯ve",     # naïve incorrectly encoded
        "rÃ©ponse",   # réponse incorrectly encoded
        "ZÃ¼rich",    # Zürich incorrectly encoded
        "dÃ©lai",     # délai incorrectly encoded
    ]
    
    for pattern in mojibake_patterns:
        assert pattern not in content, f"Mojibake pattern '{pattern}' found in output - mojibake not fixed"
    
    # These patterns should be present after fixing
    fixed_patterns = [
        "café",
        "Müller",
        "naïve",
        "réponse",
        "Zürich",
        "délai",
    ]
    
    found_fixed = 0
    for pattern in fixed_patterns:
        if pattern in content:
            found_fixed += 1
    
    assert found_fixed >= 4, f"Expected at least 4 fixed patterns, found {found_fixed}"


def test_special_characters_preserved():
    """Verify that special characters from various encodings are preserved."""
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    
    # Characters that should appear from UTF-8 files
    utf8_chars = ["François", "José García", "Sørensen", "Björk", "Łukasz"]
    found_utf8 = sum(1 for c in utf8_chars if c in content)
    assert found_utf8 >= 3, f"Expected at least 3 UTF-8 character patterns, found {found_utf8}"
    
    # Characters from Latin-1 files
    latin1_chars = ["François", "Renée", "André"]
    found_latin1 = sum(1 for c in latin1_chars if c in content)
    assert found_latin1 >= 1, f"Expected at least 1 Latin-1 character pattern, found {found_latin1}"
    
    # Characters from Windows-1252 files
    win_chars = ["Zoë", "Chloé", "Brötchen"]
    found_win = sum(1 for c in win_chars if c in content)
    assert found_win >= 1, f"Expected at least 1 Windows-1252 character pattern, found {found_win}"


def test_japanese_content_processed():
    """Verify that Shift-JIS encoded Japanese content is properly decoded."""
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    
    # Check for Japanese characters from tokyo_server.log
    # These should be present after proper Shift-JIS decoding
    japanese_markers = ["ユーザー", "ログイン", "エラー", "処理中", "完了"]
    found_japanese = sum(1 for j in japanese_markers if j in content)
    assert found_japanese >= 2, f"Expected at least 2 Japanese character patterns, found {found_japanese}"


def test_euro_symbol_processed():
    """Verify that ISO-8859-15 Euro symbol is properly decoded."""
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    
    # Check for Euro symbol from euro_orders.log
    assert "€" in content, "Euro symbol (€) not found in output - ISO-8859-15 not properly decoded"
    
    # Check for context
    euro_contexts = ["Prix:", "Kosten:", "Prezzo:", "Precio:"]
    found_contexts = sum(1 for c in euro_contexts if c in content)
    assert found_contexts >= 2, f"Expected at least 2 Euro price contexts, found {found_contexts}"


def test_log_format_preserved():
    """Verify that log line format is preserved."""
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    lines = [l for l in content.split("\n") if l.strip()]
    
    # Check that most lines have proper log format
    proper_format_count = 0
    for line in lines:
        # Format: [YYYY-MM-DD HH:MM:SS] LEVEL - message
        if line.startswith("[") and "] " in line:
            if any(f"] {level} " in line for level in ["INFO", "WARN", "ERROR", "DEBUG"]):
                proper_format_count += 1
    
    assert proper_format_count >= 320, f"Expected at least 320 properly formatted log lines, found {proper_format_count}"


def test_all_source_files_processed():
    """Verify that all source log files were processed."""
    expected_files = [
        "app_2024-03-15.log",
        "app_2024-03-15_win.log",
        "archive_corrupted.log",
        "consolidated.log",
        "euro_orders.log",
        "legacy_export.log",
        "migrated_data.log",
        "partial.log",
        "tokyo_server.log",
        "windows_app.log",
    ]
    
    for fname in expected_files:
        fpath = LOGS_DIR / fname
        assert fpath.exists(), f"Source file {fname} not found in {LOGS_DIR}"
    
    # Check that output contains content from multiple files
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    
    # Each file contributes unique timestamps/hours
    # File 1 & 2: hours 10-11 and 10-11 (UTF-8)
    # File 3: hours 14-15 (Latin-1)
    # File 4: hours 18-19 (Windows-1252)
    # File 5: hours 22-23 (Mojibake)
    # File 6: hours 2-3 (Mixed)
    # File 7: hours 6-7 (Partial)
    # File 8: hours 4-5 (Triple-encoded)
    # File 9: hours 20-21 (Shift-JIS)
    # File 10: hours 16-17 (ISO-8859-15)
    
    hour_markers = [
        "[2024-03-15 10:",  # UTF-8 files
        "[2024-03-15 14:",  # Latin-1 file
        "[2024-03-15 18:",  # Windows-1252 file
        "[2024-03-15 22:",  # Mojibake file
        "[2024-03-15 02:",  # Mixed file
        "[2024-03-15 20:",  # Shift-JIS file
        "[2024-03-15 16:",  # ISO-8859-15 file
    ]
    
    found_hours = sum(1 for h in hour_markers if h in content)
    assert found_hours >= 5, f"Expected content from at least 5 different source files, found {found_hours}"


def test_no_truncation_markers():
    """Verify that the script doesn't leave encoding error markers in output."""
    content = OUTPUT_FILE.read_text(encoding="utf-8")
    
    # These are common markers that indicate encoding errors were not properly handled
    error_markers = [
        "\ufffd",  # Unicode replacement character
        "\\x",  # Raw byte escape sequences
        "\\u",  # Unicode escape sequences in output
    ]
    
    for marker in error_markers:
        assert marker not in content, f"Encoding error marker '{repr(marker)}' found in output"
