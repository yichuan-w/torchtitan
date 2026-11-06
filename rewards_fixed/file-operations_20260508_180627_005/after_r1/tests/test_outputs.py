import re
import subprocess
import sys
from pathlib import Path

OUTPUT_FILE = Path("/app/normalized_output.txt")
LOGS_DIR = Path("/app/logs")
SCRIPT_FILE = Path("/app/normalize_encoding.py")
RUN_RESULT = None


def output_text():
    return OUTPUT_FILE.read_text(encoding="utf-8")


def test_01_run_stated_workflow_without_encoding_parameters():
    global RUN_RESULT
    OUTPUT_FILE.unlink(missing_ok=True)
    RUN_RESULT = subprocess.run(
        [sys.executable, str(SCRIPT_FILE)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/app",
    )
    assert SCRIPT_FILE.is_file() and SCRIPT_FILE.stat().st_size and RUN_RESULT.returncode == 0, (
        RUN_RESULT.stderr or "The required nonempty script did not run successfully"
    )


def test_02_output_exists_and_is_clean_utf8_lf_without_bom():
    raw = OUTPUT_FILE.read_bytes()
    text = raw.decode("utf-8")
    assert raw and b"\xef\xbb\xbf" not in raw and "\ufeff" not in text and b"\r" not in raw, (
        "Output must be nonempty clean UTF-8 using LF only and contain no BOM markers"
    )


def test_03_all_valid_lines_encodings_mojibake_and_incomplete_tail():
    text = output_text()
    lines = [line for line in text.splitlines() if line.strip()]
    required = {
        "UTF-8": ["José García", "Sørensen", "Łukasz"],
        "Latin-1": ["Renée", "André"],
        "Windows-1252": ["Zoë", "Chloé", "Brötchen"],
        "repaired mojibake": ["café", "Müller", "naïve", "réponse", "Zürich", "délai"],
    }
    bad = ("cafÃ©", "MÃ¼ller", "naÃ¯ve", "rÃ©ponse", "ZÃ¼rich", "dÃ©lai", "\ufffd", "\\x")
    complete_partial = [f"ASCII safe log entry number {index}" for index in range(25)]
    assert (
        285 <= len(lines) <= 286
        and all(sum(value in text for value in values) >= (2 if label != "repaired mojibake" else 4)
                for label, values in required.items())
        and not any(value in text for value in bad)
        and all(value in text for value in complete_partial)
    ), "Valid named-encoding lines, special characters, mojibake repair, or safe truncation is incorrect"


def test_04_complete_file_and_timestamp_order_preserved():
    source_timestamps = []
    for source in sorted(LOGS_DIR.glob("*.log")):
        source_timestamps.extend(re.findall(rb"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", source.read_bytes()))
    output_timestamps = re.findall(
        rb"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", OUTPUT_FILE.read_bytes()
    )
    without_incomplete_tail = source_timestamps[:255] + source_timestamps[256:]
    assert output_timestamps in (source_timestamps, without_incomplete_tail), (
        "Output must retain source timestamps in alphabetical filename and exact within-file order"
    )


def test_05_stdout_contains_line_count_summary():
    line_count = len([line for line in output_text().splitlines() if line.strip()])
    assert RUN_RESULT is not None and str(line_count) in re.findall(r"\d+", RUN_RESULT.stdout), (
        f"stdout does not contain generated output line count {line_count}"
    )
