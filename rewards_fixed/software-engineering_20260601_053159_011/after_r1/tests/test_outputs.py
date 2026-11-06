import os
import subprocess
import sys


def _run_make(target="", parallel=True, timeout=60):
    """Run make in /app. Returns (returncode, stdout, stderr)."""
    nproc = os.cpu_count() or 2
    cmd = ["make", "-C", "/app"]
    if parallel:
        cmd.append(f"-j{nproc}")
    if target:
        cmd.append(target)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def _assert_build_succeeds():
    """Build with make -j and verify it succeeds. Used by tests that need the binary."""
    rc, stdout, stderr = _run_make(parallel=True)
    assert rc == 0, f"make -j failed with exit code {rc}\nSTDERR:\n{stderr}\nSTDOUT:\n{stdout}"
    assert os.path.exists("/app/kvdb"), "kvdb binary was not created"


def _run_kvdb_batch(commands):
    """Run kvdb in batch mode with the given commands. Returns (returncode, stdout, stderr)."""
    input_str = "\n".join(commands)
    result = subprocess.run(
        ["/app/kvdb", "--batch"],
        input=input_str,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


# --- Build system tests ---

def test_parallel_build_succeeds():
    """make clean && make -j should succeed without errors."""
    subprocess.run(["make", "-C", "/app", "clean"], capture_output=True, timeout=30)
    _assert_build_succeeds()


def test_serial_build_succeeds():
    """make clean && make should succeed without errors."""
    subprocess.run(["make", "-C", "/app", "clean"], capture_output=True, timeout=30)
    rc, stdout, stderr = _run_make(parallel=False)
    assert rc == 0, f"make failed with exit code {rc}\nSTDERR:\n{stderr}\nSTDOUT:\n{stdout}"


def test_parallel_build_repeatable():
    """Parallel builds should succeed consistently — no intermittent race failures."""
    for attempt in range(5):
        subprocess.run(["make", "-C", "/app", "clean"], capture_output=True, timeout=30)
        rc, stdout, stderr = _run_make(parallel=True)
        assert rc == 0, (
            f"Parallel build attempt {attempt + 1} failed with exit code {rc}\n"
            f"STDERR:\n{stderr}\nSTDOUT:\n{stdout}"
        )
        assert os.path.exists("/app/kvdb"), (
            f"kvdb binary missing after build attempt {attempt + 1}"
        )


def test_clean_removes_build_artifacts():
    """make clean should remove the binary and object files."""
    # Build first
    _assert_build_succeeds()
    assert os.path.exists("/app/kvdb"), "Binary should exist before clean"

    # Clean
    subprocess.run(["make", "-C", "/app", "clean"], capture_output=True, timeout=30)

    # After clean, binary and .o files should be gone
    assert not os.path.exists("/app/kvdb"), "make clean did not remove kvdb binary"
    for f in ["main.o", "kvdb.o", "kvdb_config.h"]:
        path = os.path.join("/app", f)
        assert not os.path.exists(path), f"make clean did not remove {f}"


def test_clean_works_with_clean_file_present():
    """make clean should work even if a file named 'clean' exists (.PHONY required)."""
    # Build first
    _assert_build_succeeds()

    # Create a file named 'clean'
    clean_path = "/app/clean"
    try:
        with open(clean_path, "w") as f:
            f.write("test")

        # Run clean (should work despite the 'clean' file)
        rc, stdout, stderr = _run_make("clean", parallel=False)
        assert rc == 0, f"make clean failed: {stderr}"

        # Binary should be gone
        assert not os.path.exists("/app/kvdb"), (
            "make clean did not remove kvdb binary when 'clean' file exists "
            "(missing .PHONY?)"
        )
    finally:
        if os.path.exists(clean_path):
            os.remove(clean_path)


# --- Functional tests ---

def test_basic_put_get():
    """PUT and GET should work correctly."""
    _assert_build_succeeds()
    _, stdout, _ = _run_kvdb_batch([
        "PUT hello world",
        "GET hello",
    ])
    assert "world" in stdout, f"Expected 'world' in output, got:\n{stdout}"


def test_get_nonexistent():
    """GET on a nonexistent key should return NOTFOUND."""
    _assert_build_succeeds()
    _, stdout, _ = _run_kvdb_batch([
        "GET nonexistent",
    ])
    assert "NOTFOUND" in stdout, f"Expected NOTFOUND for missing key, got:\n{stdout}"


def test_put_overwrite():
    """PUT with an existing key should overwrite the value."""
    _assert_build_succeeds()
    _, stdout, _ = _run_kvdb_batch([
        "PUT mykey alpha",
        "PUT mykey beta",
        "GET mykey",
    ])
    assert "beta" in stdout, f"Expected 'beta' after overwrite, got:\n{stdout}"
    assert "alpha" not in stdout, f"Old value 'alpha' should not appear after overwrite:\n{stdout}"


def test_delete():
    """DEL should remove a key so subsequent GET returns NOTFOUND."""
    _assert_build_succeeds()
    _, stdout, _ = _run_kvdb_batch([
        "PUT tempkey somevalue",
        "GET tempkey",
        "DEL tempkey",
        "GET tempkey",
    ])
    assert "somevalue" in stdout, f"Expected 'somevalue' before delete, got:\n{stdout}"
    assert "NOTFOUND" in stdout, f"Expected NOTFOUND after delete, got:\n{stdout}"


def test_count():
    """COUNT should return the correct number of entries after inserts and deletes."""
    _assert_build_succeeds()
    _, stdout, _ = _run_kvdb_batch([
        "COUNT",
        "PUT a 1",
        "PUT b 2",
        "PUT c 3",
        "COUNT",
        "DEL b",
        "COUNT",
        "DEL a",
        "DEL c",
        "COUNT",
    ])
    lines = stdout.splitlines()
    expected_counts = ["0", "3", "2", "0"]
    next_expected = 0
    for line in lines:
        if next_expected < len(expected_counts) and line.strip() == expected_counts[next_expected]:
            next_expected += 1
    assert next_expected == len(expected_counts), (
        "Expected COUNT results 0, 3, 2, 0 in order; successful mutation "
        f"status lines are allowed. Got:\n{stdout}"
    )


def test_dump():
    """DUMP should list all entries in key=value format."""
    _assert_build_succeeds()
    _, stdout, _ = _run_kvdb_batch([
        "PUT foo bar",
        "PUT baz qux",
        "DUMP",
    ])
    assert "foo=bar" in stdout, f"Expected 'foo=bar' in dump, got:\n{stdout}"
    assert "baz=qux" in stdout, f"Expected 'baz=qux' in dump, got:\n{stdout}"


def test_dump_excludes_deleted_entries():
    """DUMP should not list an entry after it is deleted."""
    _assert_build_succeeds()
    _, stdout, _ = _run_kvdb_batch([
        "PUT alpha one",
        "DEL alpha",
        "COUNT",
        "DUMP",
    ])
    assert "alpha=one" not in stdout, (
        f"Deleted entry should not appear in DUMP, got:\n{stdout}"
    )


def test_delete_nonexistent_prints_notfound():
    """DEL on a nonexistent key should print NOTFOUND."""
    _assert_build_succeeds()
    _, stdout, _ = _run_kvdb_batch([
        "DEL no_such_key",
    ])
    assert "NOTFOUND" in stdout, (
        f"Expected NOTFOUND for deleting missing key, got:\n{stdout}"
    )


def test_valgrind_no_errors():
    """Running basic operations under valgrind should show no memory errors."""
    _assert_build_succeeds()
    commands = [
        "PUT alpha beta",
        "PUT gamma delta",
        "GET alpha",
        "GET gamma",
        "DEL alpha",
        "COUNT",
        "DUMP",
        "PUT epsilon zeta",
        "GET epsilon",
    ]
    input_str = "\n".join(commands)

    result = subprocess.run(
        [
            "valgrind",
            "--leak-check=full",
            "--error-exitcode=1",
            "/app/kvdb", "--batch",
        ],
        input=input_str,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"Valgrind reported errors during basic operations "
        f"(exit code {result.returncode}):\n"
        f"STDERR:\n{result.stderr}\n"
        f"STDOUT:\n{result.stdout}"
    )
    assert "ERROR SUMMARY: 0 errors" in result.stderr, (
        f"Valgrind reported errors:\n{result.stderr}"
    )


def test_long_key_no_crash():
    """The database should handle keys at the maximum length without corruption."""
    _assert_build_succeeds()
    # Key exactly 64 chars — with %64s in sscanf, this overflows a 64-byte buffer
    # (64 chars + null terminator = 65 bytes). The fix should use %63s or strncpy.
    long_key = "k" * 64
    long_val = "v" * 255

    # Run under valgrind to catch buffer overflow.
    # Use --error-exitcode=1 so any valgrind error (including program crash
    # from fortified strcpy_chk) causes non-zero exit.
    commands = [
        f"PUT {long_key} {long_val}",
        f"GET {long_key}",
        "COUNT",
    ]
    input_str = "\n".join(commands)

    result = subprocess.run(
        [
            "valgrind",
            "--leak-check=full",
            "--error-exitcode=1",
            "/app/kvdb", "--batch",
        ],
        input=input_str,
        capture_output=True,
        text=True,
        timeout=30,
    )

    # Valgrind should exit 0 and report no errors
    assert result.returncode == 0, (
        f"Valgrind/execution failed with exit {result.returncode} "
        f"(memory error or crash with long keys):\n"
        f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
    )
    assert "ERROR SUMMARY: 0 errors" in result.stderr, (
        f"Valgrind reported errors:\n{result.stderr}"
    )
