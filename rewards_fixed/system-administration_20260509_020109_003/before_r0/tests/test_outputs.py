# Test suite for logrotate recovery task

import subprocess
import os


def test_logrotate_scheduling_configured():
    """Test that a scheduled job is configured for automatic log rotation."""
    # Check for cron job in various locations
    cron_daily = os.path.exists("/etc/cron.daily/logrotate")
    cron_d = False
    if os.path.exists("/etc/cron.d/"):
        for f in os.listdir("/etc/cron.d/"):
            try:
                with open(f"/etc/cron.d/{f}", "r") as fp:
                    if "logrotate" in fp.read():
                        cron_d = True
                        break
            except:
                pass
    
    # Check crontab
    crontab_result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        timeout=30
    )
    crontab_has_logrotate = crontab_result.returncode == 0 and "logrotate" in crontab_result.stdout
    
    # Check for systemd timer symlink (more reliable than systemctl in containers)
    timer_symlink = os.path.exists("/etc/systemd/system/timers.target.wants/logrotate.timer")
    
    assert cron_daily or cron_d or crontab_has_logrotate or timer_symlink, \
        "No logrotate scheduling found. Configure cron job or systemd timer for daily rotation."


def test_logrotate_status_file_valid():
    """Test that the logrotate status file is properly formatted."""
    status_file = "/var/lib/logrotate/status"
    
    assert os.path.exists(status_file), \
        f"Status file {status_file} does not exist. Logrotate needs a valid state file."
    
    with open(status_file, "r") as f:
        content = f.read()
    
    # Must have the version header for proper logrotate operation
    assert content.strip().startswith("logrotate state -- version"), \
        f"Status file is corrupted or malformed. Should start with 'logrotate state -- version'. Got: {content[:100]}"
    
    # Should contain entries for all three log files
    assert "app.log" in content, "Status file should contain app.log entry"
    assert "access.log" in content, "Status file should contain access.log entry"
    assert "error.log" in content, "Status file should contain error.log entry"


def test_logrotate_can_rotate_without_errors():
    """Test that logrotate can run without status file errors."""
    # The status file must be valid first
    status_file = "/var/lib/logrotate/status"
    assert os.path.exists(status_file), \
        f"Status file {status_file} does not exist."
    
    with open(status_file, "r") as f:
        content = f.read()
    
    assert content.strip().startswith("logrotate state -- version"), \
        f"Status file is still corrupted. Must start with 'logrotate state -- version'."
    
    # Run logrotate to verify it works
    result = subprocess.run(
        ["logrotate", "-v", "/etc/logrotate.d/myapp"],
        capture_output=True,
        text=True,
        timeout=60
    )
    
    # Should not have status file errors
    combined = result.stdout + result.stderr
    assert "bad top line" not in combined.lower(), \
        f"Status file is still corrupted: {combined}"
    assert "error:" not in combined.lower() or "does not need rotating" in combined.lower(), \
        f"logrotate has errors: {combined}"


def test_app_log_size_based_rotation():
    """Test that app.log rotation works with size-based policy."""
    # First verify the status file is in valid state
    status_file = "/var/lib/logrotate/status"
    assert os.path.exists(status_file), \
        f"Status file {status_file} must exist."
    
    with open(status_file, "r") as f:
        status_content = f.read()
    
    assert status_content.strip().startswith("logrotate state -- version"), \
        f"Status file must be fixed before rotation can work. Got: {status_content[:100]}"
    
    # Create a fresh log with known content that exceeds 10MB
    os.makedirs("/var/log/myapp", exist_ok=True)
    test_content = "ROTATION_TEST_MARKER_LINE\n" * 500000  # ~25MB
    with open("/var/log/myapp/app.log", "w") as f:
        f.write(test_content)
    
    # Remove any existing rotated files to ensure clean state
    for f in ["/var/log/myapp/app.log.1", "/var/log/myapp/app.log.2.gz", "/var/log/myapp/app.log.1.gz"]:
        if os.path.exists(f):
            os.remove(f)
    
    # Force rotation
    result = subprocess.run(
        ["logrotate", "-f", "/etc/logrotate.d/myapp"],
        capture_output=True,
        text=True,
        timeout=60
    )
    
    assert result.returncode == 0, f"logrotate -f failed: {result.stderr}"
    
    # With copytruncate, the original file should be small
    original_size = os.path.getsize("/var/log/myapp/app.log")
    assert original_size < 1000, \
        f"After rotation, app.log should be truncated (copytruncate), but is {original_size} bytes"
    
    # The rotated file should exist
    rotated_exists = os.path.exists("/var/log/myapp/app.log.1") or \
                     os.path.exists("/var/log/myapp/app.log.1.gz")
    assert rotated_exists, "Rotated log file should exist after forced rotation"


def test_access_log_daily_rotation():
    """Test that access.log rotation works with daily policy."""
    # First verify the status file is in valid state (solver should have fixed this)
    status_file = "/var/lib/logrotate/status"
    assert os.path.exists(status_file), \
        f"Status file {status_file} must exist."
    
    with open(status_file, "r") as f:
        status_content = f.read()
    
    assert status_content.strip().startswith("logrotate state -- version"), \
        f"Status file must be fixed before rotation can work. Got: {status_content[:100]}"
    
    # Create a fresh access log with known content
    os.makedirs("/var/log/myapp", exist_ok=True)
    test_content = "ACCESS_LOG_TEST_MARKER\n" * 50
    with open("/var/log/myapp/access.log", "w") as f:
        f.write(test_content)
    
    # Remove any existing rotated files to ensure clean state
    for f in ["/var/log/myapp/access.log.1", "/var/log/myapp/access.log.2.gz", "/var/log/myapp/access.log.1.gz"]:
        if os.path.exists(f):
            os.remove(f)
    
    # Force rotation
    result = subprocess.run(
        ["logrotate", "-f", "/etc/logrotate.d/myapp"],
        capture_output=True,
        text=True,
        timeout=60
    )
    
    assert result.returncode == 0, f"logrotate -f failed: {result.stderr}"
    
    # With copytruncate, the original file should be small
    original_size = os.path.getsize("/var/log/myapp/access.log")
    assert original_size < 1000, \
        f"After rotation, access.log should be truncated (copytruncate), but is {original_size} bytes"
    
    # The rotated file should exist
    rotated_exists = os.path.exists("/var/log/myapp/access.log.1") or \
                     os.path.exists("/var/log/myapp/access.log.1.gz")
    assert rotated_exists, "Rotated access log file should exist after forced rotation"
    
    if os.path.exists("/var/log/myapp/access.log.1"):
        with open("/var/log/myapp/access.log.1", "r") as f:
            content = f.read()
        assert "ACCESS_LOG_TEST_MARKER" in content, \
            "Rotated file should contain the original log content"


def test_error_log_weekly_rotation():
    """Test that error.log rotation works with weekly policy."""
    # First verify the status file is in valid state (solver should have fixed this)
    status_file = "/var/lib/logrotate/status"
    assert os.path.exists(status_file), \
        f"Status file {status_file} must exist."
    
    with open(status_file, "r") as f:
        status_content = f.read()
    
    assert status_content.strip().startswith("logrotate state -- version"), \
        f"Status file must be fixed before rotation can work. Got: {status_content[:100]}"
    
    # Create a fresh error log with known content
    os.makedirs("/var/log/myapp", exist_ok=True)
    test_content = "ERROR_LOG_TEST_MARKER\n" * 50
    with open("/var/log/myapp/error.log", "w") as f:
        f.write(test_content)
    
    # Remove any existing rotated files to ensure clean state
    for f in ["/var/log/myapp/error.log.1", "/var/log/myapp/error.log.2.gz", "/var/log/myapp/error.log.1.gz"]:
        if os.path.exists(f):
            os.remove(f)
    
    # Force rotation
    result = subprocess.run(
        ["logrotate", "-f", "/etc/logrotate.d/myapp"],
        capture_output=True,
        text=True,
        timeout=60
    )
    
    assert result.returncode == 0, f"logrotate -f failed: {result.stderr}"
    
    # With copytruncate, the original file should be small
    original_size = os.path.getsize("/var/log/myapp/error.log")
    assert original_size < 1000, \
        f"After rotation, error.log should be truncated (copytruncate), but is {original_size} bytes"
    
    # The rotated file should exist
    rotated_exists = os.path.exists("/var/log/myapp/error.log.1") or \
                     os.path.exists("/var/log/myapp/error.log.1.gz")
    assert rotated_exists, "Rotated error log file should exist after forced rotation"


def test_logrotate_config_correct():
    """Test that the logrotate configuration has correct policies."""
    config_file = "/etc/logrotate.d/myapp"
    
    assert os.path.exists(config_file), \
        f"Configuration file {config_file} should exist"
    
    with open(config_file, "r") as f:
        content = f.read()
    
    # app.log should have size-based rotation
    assert "size 10M" in content or "size 10" in content, \
        "app.log should be configured with size-based rotation (10M)"
    
    # access.log should have daily rotation
    assert "daily" in content, \
        "access.log should be configured with daily rotation"
    
    # error.log should have weekly rotation with 4 rotations
    assert "weekly" in content, \
        "error.log should be configured with weekly rotation"
    
    # Check for rotate 4 for error.log (keeping 4 weeks)
    lines = content.split('\n')
    found_weekly_with_rotate_4 = False
    for i, line in enumerate(lines):
        if '/var/log/myapp/error.log' in line:
            # Look for rotate 4 in the following lines until we hit the closing brace
            for j in range(i+1, min(i+15, len(lines))):
                if 'rotate 4' in lines[j]:
                    found_weekly_with_rotate_4 = True
                    break
                if '}' in lines[j]:
                    break
    
    assert found_weekly_with_rotate_4, \
        "error.log should be configured with rotate 4 (keep 4 weeks)"

