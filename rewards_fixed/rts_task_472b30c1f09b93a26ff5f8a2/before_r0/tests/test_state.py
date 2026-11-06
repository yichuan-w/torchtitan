import os
import subprocess

BINARY = "/data/Crackme-Monologix-Linux"
SOLUTION = "/data/solution.txt"
CLEANUP_LOG = "/data/cleanup.log"
LEFTOVER = "/data/leftover.log"
EXPECTED_PASSWORD = "6571965"

def test_check_01_required_evidence():
    # gdb must be available
    assert subprocess.call(["which", "gdb"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0, (
        "gdb is not available; required analysis tool missing"
    )
    # cleanup.log must exist (since it's final) and contain evidence that leftover.log existed at cleanup time
    assert os.path.isfile(CLEANUP_LOG), f"missing {CLEANUP_LOG}"
    with open(CLEANUP_LOG) as f:
        lines = f.read().splitlines()
    found_leftover = any(LEFTOVER in line for line in lines)
    assert found_leftover, f"{CLEANUP_LOG} does not mention {LEFTOVER}, indicating it was not discovered or cleaned"
    found_binary = any(BINARY in line for line in lines)
    assert found_binary, f"{CLEANUP_LOG} does not mention {BINARY}, indicating it was not discovered or cleaned"

def test_check_02_intermediate_artifact():
    assert os.path.isfile(SOLUTION), f"missing solution file {SOLUTION}"
    with open(SOLUTION) as f:
        content = f.read().strip()
    assert content == EXPECTED_PASSWORD, "solution.txt does not contain the correct password"

def test_check_03_final_semantics():
    # binary must be removed
    assert not os.path.isfile(BINARY), f"binary {BINARY} still exists (should be cleaned up)"
    # leftover must be removed
    assert not os.path.isfile(LEFTOVER), f"leftover file {LEFTOVER} still exists (should be cleaned up)"
    # solution file must remain
    assert os.path.isfile(SOLUTION), f"solution file {SOLUTION} is missing"
    # cleanup log must exist and be non-empty
    assert os.path.isfile(CLEANUP_LOG), f"cleanup log {CLEANUP_LOG} is missing"
    with open(CLEANUP_LOG) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert len(lines) > 0, f"cleanup log {CLEANUP_LOG} is empty"
    # ensure both expected paths are listed
    paths_present = {line for line in lines}
    assert BINARY in paths_present, f"{CLEANUP_LOG} does not list removed binary {BINARY}"
    assert LEFTOVER in paths_present, f"{CLEANUP_LOG} does not list removed file {LEFTOVER}"

def test_check_04_no_shortcut():
    # reject if binary still exists
    assert not os.path.isfile(BINARY), f"shortcut detected: binary {BINARY} was not removed"
    # reject if leftover.log still exists
    assert not os.path.isfile(LEFTOVER), f"shortcut detected: {LEFTOVER} was not removed"
    # reject if solution.txt contains wrong password
    if os.path.isfile(SOLUTION):
        with open(SOLUTION) as f:
            content = f.read().strip()
        assert content == EXPECTED_PASSWORD, "shortcut detected: solution.txt contains wrong password"
    else:
        assert False, "solution.txt missing (cannot verify password)"
    # reject if cleanup.log is empty or missing
    assert os.path.isfile(CLEANUP_LOG), "shortcut detected: cleanup log missing"
    with open(CLEANUP_LOG) as f:
        log_contents = f.read().strip()
    assert len(log_contents) > 0, "shortcut detected: cleanup log is empty"
    # also check that log lists removed files (reject if not)
    lines = log_contents.splitlines()
    assert BINARY in lines, "shortcut detected: cleanup log missing binary path"
    assert LEFTOVER in lines, "shortcut detected: cleanup log missing leftover.log path"
