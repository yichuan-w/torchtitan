import subprocess
from pathlib import Path
import pytest


@pytest.fixture
def compiled_vm():
    """Compile the VM and return the path to the binary."""
    result = subprocess.run(
        ["gcc", "-O2", "/app/vm.c", "-o", "/app/vm"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.fail(f"VM compilation failed: {result.stderr}")
    return "/app/vm"


def test_program_exists():
    """Test that prog.bin file exists."""
    prog_path = Path("/app/prog.bin")
    assert prog_path.exists(), f"File {prog_path} does not exist"


def test_program_size():
    """Test that prog.bin is under 300 bytes."""
    prog_path = Path("/app/prog.bin")
    assert prog_path.exists(), f"File {prog_path} does not exist"
    size = prog_path.stat().st_size
    assert size < 300, f"prog.bin is {size} bytes, must be under 300 bytes"


def mod_inverse(n, mod=65537):
    """Compute modular multiplicative inverse using extended Euclidean algorithm."""
    if n == 0:
        return 0
    # Extended Euclidean algorithm
    def extended_gcd(a, b):
        if a == 0:
            return b, 0, 1
        gcd, x1, y1 = extended_gcd(b % a, a)
        x = y1 - (b // a) * x1
        y = x1
        return gcd, x, y
    
    gcd, x, _ = extended_gcd(n % mod, mod)
    if gcd != 1:
        return 0
    return x % mod


def test_mod_inverse_values(compiled_vm):
    """Test that the program correctly computes modular inverse mod 65537."""
    MOD = 65537
    test_cases = [
        (0, 0),   # no inverse for 0
        (1, 1),
        (2, 32769),
        (3, 43691),
        (4, mod_inverse(4, MOD)),
        (5, mod_inverse(5, MOD)),
        (10, mod_inverse(10, MOD)),
        (100, mod_inverse(100, MOD)),
        (1000, mod_inverse(1000, MOD)),
        (65536, 65536),
    ]
    
    for n, expected in test_cases:
        result = subprocess.run(
            ["/app/vm", "/app/prog.bin", str(n)],
            capture_output=True, text=True, timeout=60
        )
        
        assert result.returncode == 0, f"VM execution failed for input {n}: {result.stderr}"
        
        try:
            actual = int(result.stdout.strip())
        except ValueError:
            assert False, f"Could not parse output for input {n}: '{result.stdout}'"
        
        # Verify that the answer is correct
        if expected == 0:
            assert actual == 0, f"For input n={n}: expected 0, got {actual}"
        else:
            # Check that actual * n ≡ 1 (mod 65537)
            computed = (actual * n) % MOD
            assert computed == 1, f"For input n={n}: got {actual} but {actual} * {n} mod {MOD} = {computed}, not 1"


def test_mod_inverse_additional(compiled_vm):
    """Test additional modular inverse cases."""
    MOD = 65537
    test_cases = [
        (12345, mod_inverse(12345, MOD)),
        (50000, mod_inverse(50000, MOD)),
        (32768, mod_inverse(32768, MOD)),
    ]
    
    for n, expected in test_cases:
        result = subprocess.run(
            ["/app/vm", "/app/prog.bin", str(n)],
            capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, f"VM execution failed for input {n}: {result.stderr}"
        actual = int(result.stdout.strip())
        computed = (actual * n) % MOD
        assert computed == 1, f"For input n={n}: got {actual} but {actual} * {n} mod {MOD} = {computed}, not 1"
