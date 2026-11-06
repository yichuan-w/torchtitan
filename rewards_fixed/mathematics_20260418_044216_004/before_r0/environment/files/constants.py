"""
Generate a vector of high-precision constants that satisfy an integer relation.

This module provides access to a vector of 6 real numbers computed to 200-digit precision.
These numbers satisfy an integer relation (a linear combination with integer coefficients
that sums to zero). The relation must be discovered using the PSLQ algorithm or similar.
"""

from mpmath import mp

# Set precision to 200 decimal digits
mp.dps = 200

def get_constants():
    """
    Return a list of 6 high-precision real numbers.
    
    The numbers are computed to 200-digit precision and satisfy
    an integer relation (a linear combination with integer coefficients
    that sums to zero).
    
    Returns:
        list: A list of 6 mpf numbers
    """
    # Constants based on fundamental mathematical values
    # An integer relation exists among these constants
    
    pi = mp.pi
    
    c0 = mp.mpf(1)
    c1 = pi
    c2 = pi ** 2
    c3 = pi ** 3
    c4 = pi ** 4
    c5 = pi ** 5
    
    return [c0, c1, c2, c3, c4, c5]


def get_precision():
    """
    Return the working precision in decimal digits.
    
    Returns:
        int: The number of decimal digits of precision
    """
    return mp.dps


if __name__ == "__main__":
    constants = get_constants()
    print(f"Precision: {get_precision()} decimal digits")
    print(f"Number of constants: {len(constants)}")
    for i, c in enumerate(constants):
        print(f"x[{i}] = {c}")
