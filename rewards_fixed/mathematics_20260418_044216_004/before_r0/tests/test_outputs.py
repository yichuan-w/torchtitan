import os
import sys
from pathlib import Path
import math

sys.path.append("/app")

# Change the constants file to prevent reward hacking.
# We use specific hidden coefficients that define the integer relation.
HIDDEN_COEFFICIENTS = [17, -29, 37, -47, 53, -67]

open("/app/constants.py", "w").write('''
from mpmath import mp

mp.dps = 200

def get_constants():
    """
    Return a list of 6 high-precision real numbers that satisfy an integer relation.
    """
    # Base constants: fundamental algebraic values
    x0 = mp.mpf(1)
    x1 = mp.sqrt(2)
    x2 = mp.sqrt(3)
    x3 = mp.sqrt(5)
    x4 = mp.sqrt(7)
    
    # The 6th constant is computed to satisfy a hidden integer relation
    # among all 6 constants
    partial = 17*x0 - 29*x1 + 37*x2 - 47*x3 + 53*x4
    x5 = partial / 67
    
    return [x0, x1, x2, x3, x4, x5]

def get_precision():
    return mp.dps
''')


def normalize_coefficients(coeffs):
    """Normalize coefficients: gcd=1, first non-zero is positive."""
    # Remove leading/trailing zeros for gcd calculation
    non_zero = [c for c in coeffs if c != 0]
    if not non_zero:
        return coeffs
    
    # Compute gcd
    from math import gcd
    g = abs(non_zero[0])
    for c in non_zero[1:]:
        g = gcd(g, abs(c))
    
    # Normalize by gcd
    normalized = [c // g for c in coeffs]
    
    # Make first non-zero positive
    for c in normalized:
        if c != 0:
            if c < 0:
                normalized = [-x for x in normalized]
            break
    
    return normalized


def test_relation_file_exists():
    """Test that the relation file was created."""
    # Run the solver's program
    import subprocess
    result = subprocess.run(
        ["python3", "/app/find_relation.py"],
        capture_output=True, text=True, timeout=300
    )
    
    relation_path = Path("/app/relation.txt")
    assert relation_path.exists(), f"File {relation_path} does not exist. stdout: {result.stdout}, stderr: {result.stderr}"


def test_relation_format():
    """Test that the relation file has the correct format."""
    import subprocess
    result = subprocess.run(
        ["python3", "/app/find_relation.py"],
        capture_output=True, text=True, timeout=300
    )
    
    relation_path = Path("/app/relation.txt")
    assert relation_path.exists(), f"File {relation_path} does not exist"
    
    with open(relation_path, 'r') as f:
        lines = f.readlines()
    
    assert len(lines) == 6, f"Expected 6 lines, got {len(lines)}"
    
    coeffs = []
    for i, line in enumerate(lines):
        try:
            coeffs.append(int(line.strip()))
        except ValueError:
            assert False, f"Line {i} is not a valid integer: {line.strip()}"


def test_relation_correctness():
    """Test that the discovered relation is correct."""
    import subprocess
    result = subprocess.run(
        ["python3", "/app/find_relation.py"],
        capture_output=True, text=True, timeout=300
    )
    
    relation_path = Path("/app/relation.txt")
    assert relation_path.exists(), f"File {relation_path} does not exist"
    
    with open(relation_path, 'r') as f:
        lines = f.readlines()
    
    coeffs = [int(line.strip()) for line in lines]
    
    # Normalize the discovered coefficients
    normalized = normalize_coefficients(coeffs)
    
    # Normalize the expected coefficients
    expected_normalized = normalize_coefficients(HIDDEN_COEFFICIENTS)
    
    assert normalized == expected_normalized, f"Expected {expected_normalized}, got {normalized}"


def test_relation_satisfies_equation():
    """Test that the relation actually satisfies the equation to high precision."""
    import subprocess
    result = subprocess.run(
        ["python3", "/app/find_relation.py"],
        capture_output=True, text=True, timeout=300
    )
    
    # Import the constants
    from constants import get_constants
    
    relation_path = Path("/app/relation.txt")
    assert relation_path.exists(), f"File {relation_path} does not exist"
    
    with open(relation_path, 'r') as f:
        coeffs = [int(line.strip()) for line in f.readlines()]
    
    constants = get_constants()
    
    # Compute the linear combination
    total = sum(c * x for c, x in zip(coeffs, constants))
    
    # The result should be very close to zero (within numerical precision)
    # Since we use 200-digit precision, we expect at least 100 digits of accuracy
    from mpmath import mp
    mp.dps = 200
    
    assert abs(total) < mp.mpf('1e-100'), f"Relation does not satisfy equation: sum = {total}"

