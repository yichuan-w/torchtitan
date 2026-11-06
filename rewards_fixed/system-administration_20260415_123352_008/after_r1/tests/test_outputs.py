"""
Tests for cron job debugging task.
Verifies that all three cron jobs are properly fixed and functional.
"""

import os
import subprocess
import time


def test_backup_script_works():
    """Test that backup.sh runs successfully and creates backup files."""
    # Remove any existing backups for clean test
    backup_dir = "/var/backups/app"
    if os.path.exists(backup_dir):
        for f in os.listdir(backup_dir):
            if f.startswith("backup-") and f.endswith(".tar.gz"):
                os.remove(os.path.join(backup_dir, f))
    
    # Run the backup script
    result = subprocess.run(
        ["/usr/local/bin/backup.sh"],
        capture_output=True,
        text=True,
        timeout=30
    )
    
    # Check that script executed without syntax errors
    assert result.returncode == 0, f"Backup script failed with return code {result.returncode}. stderr: {result.stderr}"
    
    # Check that backup files were created
    backup_files = [f for f in os.listdir(backup_dir) if f.startswith("backup-") and f.endswith(".tar.gz")]
    assert len(backup_files) > 0, f"No backup files found in {backup_dir}"
    
    # Verify backup contains expected data
    import tarfile
    backup_path = os.path.join(backup_dir, backup_files[0])
    with tarfile.open(backup_path, 'r:gz') as tar:
        names = tar.getnames()
        # Check for config.txt in any form (could be ./config.txt or config.txt)
        has_config = any("config.txt" in name for name in names)
        assert has_config, f"Backup missing config.txt. Contents: {names}"


def test_cleanup_script_works():
    """Test that cleanup.sh runs successfully as appuser."""
    # Create test files to clean up (old files)
    import datetime
    
    tmp_dir = "/var/cache/app/tmp"
    log_dir = "/var/log/app"
    
    # Create an old temp file
    old_tmp = os.path.join(tmp_dir, "test_old.tmp")
    with open(old_tmp, 'w') as f:
        f.write("old temp data")
    # Set modification time to 5 days ago
    import time as t
    old_time = t.time() - (5 * 24 * 60 * 60)
    os.utime(old_tmp, (old_time, old_time))
    
    # Create an old log file
    old_log = os.path.join(log_dir, "test_old.log")
    with open(old_log, 'w') as f:
        f.write("old log data")
    old_time_log = t.time() - (2 * 24 * 60 * 60)
    os.utime(old_log, (old_time_log, old_time_log))
    
    # Run cleanup as appuser
    result = subprocess.run(
        ["su", "-", "appuser", "-c", "/usr/local/bin/cleanup.sh"],
        capture_output=True,
        text=True,
        timeout=30
    )
    
    # Check that script executed without permission errors
    assert result.returncode == 0, f"Cleanup script failed as appuser. stderr: {result.stderr}"
    assert "Permission denied" not in result.stderr, f"Permission denied error: {result.stderr}"
    
    # Check cleanup log was written
    cleanup_log = "/var/log/cleanup.log"
    assert os.path.exists(cleanup_log), f"Cleanup log not found at {cleanup_log}"
    with open(cleanup_log, 'r') as f:
        content = f.read()
    assert "Cleaned temp files" in content or "Compressed" in content, f"Cleanup log doesn't show successful execution: {content}"

def test_healthcheck_script_works():
    """Test that healthcheck.sh runs successfully and writes status."""
    status_dir = "/var/run/app"
    health_log = "/var/log/healthcheck.log"

    # Clear prior regular files so the check observes this execution's output.
    os.makedirs(status_dir, exist_ok=True)
    for name in os.listdir(status_dir):
        path = os.path.join(status_dir, name)
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
    
    # Run healthcheck as root (as configured in cron)
    result = subprocess.run(
        ["/usr/local/bin/healthcheck.sh"],
        capture_output=True,
        text=True,
        timeout=30
    )
    
    # Check that script executed without permission errors
    assert result.returncode in [0, 1], f"Healthcheck script failed unexpectedly. stderr: {result.stderr}"
    assert "Permission denied" not in result.stderr, f"Permission denied error: {result.stderr}"
    
    # The instruction fixes the directory, but deliberately leaves the filename open.
    status_contents = []
    for name in os.listdir(status_dir):
        path = os.path.join(status_dir, name)
        if os.path.isfile(path):
            with open(path, 'r', errors='replace') as status:
                status_contents.append(status.read())
    assert status_contents, f"No status file created in {status_dir}"
    matching_status = next(
        (
            content for content in status_contents
            if "myapp" in content.lower()
            and ("healthy" in content.lower() or "unhealthy" in content.lower())
        ),
        None,
    )
    assert matching_status is not None, f"Runtime status missing app health: {status_contents}"
    
    # Check health log was appended (root should be able to append)
    assert os.path.exists(health_log), f"Health log not found at {health_log}"
    with open(health_log, 'r') as f:
        log_content = f.read()
    assert "Health check" in log_content, f"Health log missing check entry: {log_content}"


def test_cron_configuration_valid():
    """Test that cron configuration is syntactically correct and jobs are properly configured."""
    crontab_file = "/etc/cron.d/app-jobs"
    
    # Check file exists and is readable
    assert os.path.exists(crontab_file), f"Crontab file not found: {crontab_file}"
    
    with open(crontab_file, 'r') as f:
        content = f.read()
    
    # Check all three jobs are present
    assert "backup.sh" in content, "Backup job missing from crontab"
    assert "cleanup.sh" in content, "Cleanup job missing from crontab"
    assert "healthcheck.sh" in content, "Healthcheck job missing from crontab"

    active_lines = [line for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert all(
        any(script in line and "/var/log/cron.log" in line for line in active_lines)
        for script in ("backup.sh", "cleanup.sh", "healthcheck.sh")
    ), f"Each cron job must log to /var/log/cron.log. Crontab content:\n{content}"
    
    # Check that bash syntax is handled - either via crontab SHELL or script shebang
    # Option 1: Crontab has SHELL=/bin/bash
    has_bash_shell = "SHELL=/bin/bash" in content
    # Option 2: Crontab calls scripts with /bin/bash
    has_bash_call = "/bin/bash" in content and "backup.sh" in content
    
    # Option 3: Check the backup script shebang
    backup_script = "/usr/local/bin/backup.sh"
    if os.path.exists(backup_script):
        with open(backup_script, 'r') as f:
            first_line = f.readline().strip()
        has_bash_shebang = first_line == "#!/bin/bash"
    else:
        has_bash_shebang = False
    
    # At least one approach should be used to handle bash syntax
    assert has_bash_shell or has_bash_call or has_bash_shebang, (
        f"Cron configuration doesn't handle bash syntax properly.\n"
        f"Either set SHELL=/bin/bash in crontab, call scripts with /bin/bash, "
        f"or change script shebang to #!/bin/bash.\n"
        f"Crontab content:\n{content}"
    )


def test_permissions_fixed():
    """Test that permission issues are resolved."""
    # Check that appuser can write to tmp directory
    result = subprocess.run(
        ["su", "-", "appuser", "-c", "touch /var/cache/app/tmp/test_write.tmp"],
        capture_output=True,
        text=True,
        timeout=10
    )
    assert result.returncode == 0, f"appuser cannot write to /var/cache/app/tmp: {result.stderr}"
    
    # Check that appuser can write to log directory
    result = subprocess.run(
        ["su", "-", "appuser", "-c", "touch /var/log/app/test_write.log"],
        capture_output=True,
        text=True,
        timeout=10
    )
    assert result.returncode == 0, f"appuser cannot write to /var/log/app: {result.stderr}"
