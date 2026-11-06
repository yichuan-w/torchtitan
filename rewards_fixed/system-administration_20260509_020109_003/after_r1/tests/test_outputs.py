# Test suite for logrotate recovery task

import gzip
import os
from pathlib import Path
import subprocess


CONFIG = "/etc/logrotate.d/myapp"
LOG_DIR = Path("/var/log/myapp")
LOG_NAMES = ("app.log", "access.log", "error.log")


def run_logrotate(*arguments):
    return subprocess.run(
        ["logrotate", *arguments, CONFIG],
        capture_output=True,
        text=True,
        timeout=60,
    )


def clear_archives():
    for log_name in LOG_NAMES:
        for path in LOG_DIR.glob(f"{log_name}*"):
            if path.name != log_name:
                path.unlink()


def archive_contains(log_name, marker):
    marker_bytes = marker.encode()
    for path in LOG_DIR.glob(f"{log_name}*"):
        if path.name == log_name or not path.is_file():
            continue
        try:
            content = gzip.open(path, "rb").read() if path.suffix == ".gz" else path.read_bytes()
        except (OSError, EOFError):
            continue
        if marker_bytes in content:
            return True
    return False


def test_logrotate_scheduling_configured():
    """Test that a scheduled job is configured for automatic log rotation."""
    cron_daily = os.path.exists("/etc/cron.daily/logrotate")
    cron_d = False
    if os.path.exists("/etc/cron.d/"):
        for filename in os.listdir("/etc/cron.d/"):
            try:
                with open(f"/etc/cron.d/{filename}", "r") as cron_file:
                    if "logrotate" in cron_file.read():
                        cron_d = True
                        break
            except OSError:
                pass

    crontab_result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, timeout=30
    )
    crontab_has_logrotate = (
        crontab_result.returncode == 0 and "logrotate" in crontab_result.stdout
    )
    timer_symlink = os.path.exists(
        "/etc/systemd/system/timers.target.wants/logrotate.timer"
    )

    assert cron_daily or cron_d or crontab_has_logrotate or timer_symlink, \
        "No logrotate scheduling found. Configure cron job or systemd timer for daily rotation."


def test_logrotate_status_file_valid():
    """Test that persisted logrotate state does not prevent operation."""
    result = run_logrotate("-v")
    assert result.returncode == 0, \
        f"logrotate could not use its configured state: {result.stdout}{result.stderr}"


def test_logrotate_can_rotate_without_errors():
    """Test that logrotate can run without status file errors."""
    result = run_logrotate("-v")
    combined = result.stdout + result.stderr
    assert "bad top line" not in combined.lower(), f"Status file is corrupted: {combined}"
    assert result.returncode == 0, f"logrotate has errors: {combined}"


def check_single_rotation(log_name, marker, repetitions):
    clear_archives()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / log_name).write_text(marker * repetitions)
    result = run_logrotate("-f")
    assert result.returncode == 0, f"logrotate -f failed: {result.stderr}"
    assert archive_contains(log_name, marker), \
        f"An archive of {log_name} should contain its original content"


def test_app_log_size_based_rotation():
    """Test that app.log rotation works with size-based policy."""
    check_single_rotation("app.log", "ROTATION_TEST_MARKER_LINE\n", 500000)


def test_access_log_daily_rotation():
    """Test that access.log rotation works with daily policy."""
    check_single_rotation("access.log", "ACCESS_LOG_TEST_MARKER\n", 50)


def test_error_log_weekly_rotation():
    """Test that error.log rotation works with weekly policy."""
    check_single_rotation("error.log", "ERROR_LOG_TEST_MARKER\n", 50)


def test_logrotate_config_correct():
    """Test that the logrotate configuration has correct policies."""
    config_file = Path(CONFIG)
    assert config_file.exists(), f"Configuration file {config_file} should exist"
    content = config_file.read_text()

    assert "size 10M" in content or "size 10" in content, \
        "app.log should be configured with size-based rotation (10M)"
    assert "daily" in content, "access.log should be configured with daily rotation"
    assert "weekly" in content, "error.log should be configured with weekly rotation"

    lines = content.splitlines()
    found_weekly_with_rotate_4 = False
    for index, line in enumerate(lines):
        if "/var/log/myapp/error.log" in line:
            for following in lines[index + 1:index + 15]:
                if "rotate 4" in following:
                    found_weekly_with_rotate_4 = True
                    break
                if "}" in following:
                    break

    assert found_weekly_with_rotate_4, \
        "error.log should be configured with rotate 4 (keep 4 weeks)"


def test_repeated_rotations_preserve_every_archive():
    """Test that consecutive eligible rotations retain both payloads for every log."""
    clear_archives()
    first_markers = {name: f"FIRST_ARCHIVE_{name}\n" for name in LOG_NAMES}
    second_markers = {name: f"SECOND_ARCHIVE_{name}\n" for name in LOG_NAMES}

    for log_name, marker in first_markers.items():
        repetitions = 600000 if log_name == "app.log" else 50
        (LOG_DIR / log_name).write_text(marker * repetitions)
    first_result = run_logrotate("-f")
    assert first_result.returncode == 0, \
        f"First eligible rotation failed: {first_result.stderr}"

    for log_name, marker in second_markers.items():
        repetitions = 600000 if log_name == "app.log" else 50
        (LOG_DIR / log_name).write_text(marker * repetitions)
    second_result = run_logrotate("-f")
    assert second_result.returncode == 0, \
        f"Second eligible rotation failed: {second_result.stderr}"

    missing = [
        f"{log_name}:{generation}"
        for log_name in LOG_NAMES
        for generation, marker in (
            ("first", first_markers[log_name]),
            ("second", second_markers[log_name]),
        )
        if not archive_contains(log_name, marker)
    ]
    assert not missing, \
        f"Repeated rotations overwrote or skipped archived payloads: {', '.join(missing)}"
