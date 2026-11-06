"""
"""

import subprocess
import os
import pytest


def compile_reader():
    """Compile the reader program. Raises pytest.fail on compilation error."""
    result = subprocess.run(
        ["make", "-C", "/app", "clean"],
        capture_output=True, text=True, timeout=30,
    )
    result = subprocess.run(
        ["make", "-C", "/app"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(
            f"Failed to compile reader:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    executable = "/app/reader"
    assert os.path.exists(executable), "Reader executable was not created"
    return executable


def test_malformed_file_does_not_crash():
    """The program should process malformed.dat (which triggers the library bug)
    without crashing. The solver must work around the bug in the shared library."""
    executable = compile_reader()
    result = subprocess.run(
        [executable, "/app/malformed.dat"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode >= 0, (
        f"Reader crashed on malformed.dat (killed by signal {-result.returncode}):\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "Dataset: 2 items" in result.stdout, (
        f"Expected header parsing to succeed. Got:\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_no_valgrind_heap_errors():
    """Processing malformed.dat under valgrind should report no heap errors."""
    executable = compile_reader()
    valgrind_cmd = [
        "valgrind",
        "--leak-check=full",
        "--error-exitcode=1",
        "--errors-for-leak-kinds=all",
        executable,
        "/app/malformed.dat",
    ]
    result = subprocess.run(
        valgrind_cmd,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"Valgrind detected memory errors:\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_both_files_process_in_sequence():
    """After the fix, processing malformed.dat should not corrupt state such that
    normal.dat subsequently fails. We invoke the reader twice."""
    executable = compile_reader()
    
    # Process malformed.dat first
    result1 = subprocess.run(
        [executable, "/app/malformed.dat"],
        capture_output=True, text=True, timeout=60,
    )
    assert result1.returncode >= 0, (
        f"Reader crashed on malformed.dat (signal {-result1.returncode}):\n"
        f"STDOUT: {result1.stdout}\nSTDERR: {result1.stderr}"
    )
    
    # Then process normal.dat
    result2 = subprocess.run(
        [executable, "/app/normal.dat"],
        capture_output=True, text=True, timeout=30,
    )
    assert result2.returncode == 0, (
        f"Reader failed on normal.dat after processing malformed.dat "
        f"(exit {result2.returncode}):\n"
        f"STDOUT: {result2.stdout}\nSTDERR: {result2.stderr}"
    )
    assert "Dataset: 3 items" in result2.stdout, (
        f"Expected 3 items. Got:\nSTDOUT: {result2.stdout}"
    )

