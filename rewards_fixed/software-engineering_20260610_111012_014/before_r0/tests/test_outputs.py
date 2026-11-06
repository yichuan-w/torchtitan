import subprocess
import os


EXPECTED_OUTPUT_1 = """\
=== Multiplication Table (5x5) ===
1\t2\t3\t4\t5
2\t4\t6\t8\t10
3\t6\t9\t12\t15
4\t8\t12\t16\t20
5\t10\t15\t20\t25
=== Primes up to 50 ===
2 3 5 7 11 13 17 19 23 29 31 37 41 43 47
=== Factorials 1-10 ===
1! = 1
2! = 2
3! = 6
4! = 24
5! = 120
6! = 720
7! = 5040
8! = 40320
9! = 362880
10! = 3628800
=== Done ===
"""

EXPECTED_OUTPUT_2 = """\
a=17
b=23
c=391
d=431
sum=150
"""


def _run_interpreter(vm_path, luac_path):
    """Run the interpreter and return stdout, stderr, and return code."""
    result = subprocess.run(
        ["python3", vm_path, luac_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout, result.stderr, result.returncode


def test_interpreter_exists():
    """Test that luavm.py exists and is executable."""
    vm_path = "/app/luavm.py"
    assert os.path.exists(vm_path), f"Expected {vm_path} to exist"
    assert os.path.isfile(vm_path), f"Expected {vm_path} to be a file"


def test_interpreter_program1():
    """Test the interpreter on program.luac produces expected output."""
    vm_path = "/app/luavm.py"
    luac_path = "/app/program.luac"

    assert os.path.exists(luac_path), f"Bytecode file {luac_path} not found"

    stdout, stderr, rc = _run_interpreter(vm_path, luac_path)

    stdout_normalized = stdout.rstrip()
    expected_normalized = EXPECTED_OUTPUT_1.rstrip()

    assert stdout_normalized == expected_normalized, (
        f"Output mismatch for program.luac.\n"
        f"--- Expected (len={len(expected_normalized)}) ---\n"
        f"{expected_normalized}\n"
        f"--- Got (len={len(stdout_normalized)}) ---\n"
        f"{stdout_normalized}\n"
        f"--- Stderr ---\n"
        f"{stderr}\n"
    )


def test_interpreter_program2():
    """Test the interpreter on prog2.luac produces expected output."""
    vm_path = "/app/luavm.py"
    luac_path = "/app/prog2.luac"

    assert os.path.exists(luac_path), f"Bytecode file {luac_path} not found"

    stdout, stderr, rc = _run_interpreter(vm_path, luac_path)

    stdout_normalized = stdout.rstrip()
    expected_normalized = EXPECTED_OUTPUT_2.rstrip()

    assert stdout_normalized == expected_normalized, (
        f"Output mismatch for prog2.luac.\n"
        f"--- Expected (len={len(expected_normalized)}) ---\n"
        f"{expected_normalized}\n"
        f"--- Got (len={len(stdout_normalized)}) ---\n"
        f"{stdout_normalized}\n"
        f"--- Stderr ---\n"
        f"{stderr}\n"
    )