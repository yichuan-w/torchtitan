import os
import re
import subprocess
import pytest


@pytest.fixture(autouse=True)
def reset_config():
    """Ensure /root/config.txt contains '100' before each test."""
    with open("/root/config.txt", "w") as f:
        f.write("100")


def run_program():
    """Run the prime generator executable, falling back to QEMU if needed."""
    executable = "/root/generador-primos"
    try:
        result = subprocess.run(
            [executable],
            capture_output=True,
            text=True,
            timeout=10
        )
    except OSError:
        # Exec format error – try QEMU static binaries
        for qemu in ["qemu-aarch64", "qemu-aarch64-static"]:
            try:
                result = subprocess.run(
                    [qemu, executable],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                break
            except FileNotFoundError:
                continue
        else:
            raise RuntimeError("Cannot run ARM64 binary natively or via QEMU")
    return result.stdout, result.stderr


def test_source_file_exists_and_uses_config():
    """Source must exist and reference the config file while avoiding C library."""
    path = "/root/generador-primos.s"
    assert os.path.isfile(path), f"Source file {path} not found"
    with open(path, "r") as f:
        content = f.read()
    assert "/root/config.txt" in content, (
        "Source must reference the path /root/config.txt"
    )
    assert "printf" not in content, "Program must not use printf (C library)"
    assert "scanf" not in content, "Program must not use scanf (C library)"
    assert re.search(r'\bsvc\b', content, re.IGNORECASE), (
        "Program must use the svc instruction for Linux syscalls"
    )


def test_completed_starter_source_builds_and_computes_primes(tmp_path):
    """The submitted starter source itself must assemble into the working program."""
    object_path = tmp_path / "generador-primos.o"
    executable_path = tmp_path / "generador-primos"
    build = subprocess.run(
        [
            "bash", "-c",
            "aarch64-linux-gnu-as /root/generador-primos.s -o \"$1\" && "
            "aarch64-linux-gnu-ld \"$1\" -o \"$2\"",
            "build-source", str(object_path), str(executable_path)
        ],
        capture_output=True,
        text=True,
        timeout=10
    )
    assert build.returncode == 0, (
        f"Starter source does not assemble and link: {build.stderr}"
    )

    result = subprocess.run(
        ["qemu-aarch64", str(executable_path)],
        capture_output=True,
        text=True,
        timeout=10
    )
    lines = result.stdout.rstrip('\n').split('\n')
    normalized = [lines[0], ' '.join(lines[1].split())] if len(lines) == 2 else lines
    assert normalized == [
        "Números primos hasta 100:",
        "2 3 5 7 11 13 17 19 23 29 31 37 41 43 47 53 59 61 67 71 73 79 83 89 97"
    ], f"Executable rebuilt from the starter source produced: {result.stdout!r}"


def test_executable_exists_and_is_runnable():
    """Executable must exist and have execute permission."""
    exe = "/root/generador-primos"
    assert os.path.isfile(exe), "Executable /root/generador-primos missing"
    assert os.access(exe, os.X_OK), "Executable must be marked as executable"


def test_default_limit_100():
    """With default config (100) the output matches the expected format."""
    stdout, stderr = run_program()
    assert "Números primos hasta 100:" in stdout, "Header line missing"

    lines = stdout.rstrip('\n').split('\n')
    assert len(lines) == 2, (
        "Expected exactly two lines: header and primes line"
    )
    assert lines[0] == "Números primos hasta 100:", (
        f"Unexpected header: {lines[0]}"
    )

    expected_primes = (
        "2 3 5 7 11 13 17 19 23 29 31 37 41 43 47 53 59 61 67 71 73 79 83 89 97"
    )
    primes_clean = ' '.join(lines[1].split())
    assert primes_clean == expected_primes, (
        f"Expected primes '{expected_primes}', got '{primes_clean}'"
    )


def test_limit_50_after_config_change():
    """Modifying config to 50 without recompiling yields correct output."""
    with open("/root/config.txt", "w") as f:
        f.write("50")
    stdout, stderr = run_program()
    assert "Números primos hasta 50:" in stdout, "Header for limit 50 missing"

    lines = stdout.rstrip('\n').split('\n')
    assert len(lines) == 2, "Expected exactly two lines for limit 50"
    assert lines[0] == "Números primos hasta 50:"

    expected_primes = "2 3 5 7 11 13 17 19 23 29 31 37 41 43 47"
    primes_clean = ' '.join(lines[1].split())
    assert primes_clean == expected_primes, (
        f"For limit 50, expected primes '{expected_primes}', got '{primes_clean}'"
    )


def test_limit_2_only_outputs_2():
    """Edge case: limit=2 prints header and only the prime 2."""
    with open("/root/config.txt", "w") as f:
        f.write("2")
    stdout, stderr = run_program()
    assert "Números primos hasta 2:" in stdout, "Header for limit 2 missing"

    lines = stdout.rstrip('\n').split('\n')
    assert len(lines) == 2, "Expected two lines: header and '2'"
    assert lines[0] == "Números primos hasta 2:"
    assert lines[1].strip() == "2", f"Expected only '2', got {lines[1]}"


def test_limit_1_outputs_header_only():
    """Edge case: limit=1 prints header and no primes."""
    with open("/root/config.txt", "w") as f:
        f.write("1")
    stdout, stderr = run_program()
    stripped = stdout.rstrip('\n')
    assert stripped == "Números primos hasta 1:", (
        f"Expected only header 'Números primos hasta 1:', got '{stripped}'"
    )
    # There must not be any primes after the header
    # The output should be exactly the header line followed by a newline.
    assert not stdout[len(stripped):].startswith("\n "), (
        "No primes should follow the header line"
    )
