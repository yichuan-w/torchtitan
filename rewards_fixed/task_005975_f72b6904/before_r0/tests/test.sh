#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py

import os
import stat
import subprocess
import re
import pytest

def test_hardened_users_txt():
    """Verify /home/user/hardened_users.txt is correct and has 0444 permissions."""
    hardened_path = "/home/user/hardened_users.txt"
    assert os.path.isfile(hardened_path), f"{hardened_path} does not exist."

    # Check permissions
    st = os.stat(hardened_path)
    perms = stat.S_IMODE(st.st_mode)
    assert perms == 0o444, f"Expected permissions 0444 for {hardened_path}, got {oct(perms)}"

    # Check contents
    expected_contents = (
        "alice:wheel:/bin/bash\n"
        "bob:developers:/usr/sbin/nologin\n"
        "charlie:interns:/usr/sbin/nologin\n"
        "david:wheel:/bin/zsh\n"
        "eve:contractors:/usr/sbin/nologin"
    )
    with open(hardened_path, "r") as f:
        actual_contents = f.read().strip()

    assert actual_contents == expected_contents, f"Contents of {hardened_path} do not match the expected hardened state."

def test_setup_env_idempotence():
    """Verify that running /home/user/setup_env.sh again does not change the correct output."""
    script_path = "/home/user/setup_env.sh"
    hardened_path = "/home/user/hardened_users.txt"

    assert os.path.isfile(script_path), f"{script_path} does not exist."

    # Run the script again
    result = subprocess.run(["bash", script_path], capture_output=True, text=True)
    assert result.returncode == 0, f"Running {script_path} failed with error: {result.stderr}"

    # Check contents again
    expected_contents = (
        "alice:wheel:/bin/bash\n"
        "bob:developers:/usr/sbin/nologin\n"
        "charlie:interns:/usr/sbin/nologin\n"
        "david:wheel:/bin/zsh\n"
        "eve:contractors:/usr/sbin/nologin"
    )
    with open(hardened_path, "r") as f:
        actual_contents = f.read().strip()

    assert actual_contents == expected_contents, "Running setup_env.sh a second time altered the output file incorrectly (not idempotent)."

def test_start_vm_script():
    """Verify /home/user/start_vm.sh has 0755 permissions and correct QEMU arguments."""
    script_path = "/home/user/start_vm.sh"
    assert os.path.isfile(script_path), f"{script_path} does not exist."

    # Check permissions
    st = os.stat(script_path)
    perms = stat.S_IMODE(st.st_mode)
    assert perms == 0o755, f"Expected permissions 0755 for {script_path}, got {oct(perms)}"

    with open(script_path, "r") as f:
        content = f.read()

    assert "qemu-system-x86_64" in content, "qemu-system-x86_64 command not found in start_vm.sh"
    assert re.search(r"-m\s+512M?", content), "-m 512 or -m 512M not found in start_vm.sh"
    assert re.search(r"-hda\s+/home/user/disk\.qcow2", content), "-hda /home/user/disk.qcow2 not found in start_vm.sh"
    assert "-nographic" in content, "-nographic not found in start_vm.sh"
    assert re.search(r"-vnc\s+127\.0\.0\.1:2,password=on", content), "-vnc 127.0.0.1:2,password=on not found in start_vm.sh"

def test_cron_job():
    """Verify the cron job is correctly scheduled."""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    assert result.returncode == 0, "Failed to read crontab"

    crontab_output = result.stdout

    # Look for the expected cron schedule and command
    expected_pattern = r"15\s+3\s+\*\s+\*\s+\*\s+/home/user/setup_env\.sh"
    assert re.search(expected_pattern, crontab_output), "Cron job for /home/user/setup_env.sh at 3:15 AM not found in crontab."
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
