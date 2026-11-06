"""
Number theory library for modular arithmetic.

Provides basic number-theoretic functions used by the elliptic curve
cryptography module.
"""


def gcd(a: int, b: int) -> int:
    """Compute the greatest common divisor of a and b."""
    while b != 0:
        a, b = b, a % b
    return abs(a)


def egcd(a: int, b: int) -> tuple:
    """Extended Euclidean algorithm.

    Returns (g, x, y) such that a*x + b*y = g = gcd(a, b).
    """
    if a == 0:
        return b, 0, 1
    g, x1, y1 = egcd(b % a, a)
    return g, y1 - (b // a) * x1, x1


def modinv(a: int, m: int) -> int:
    """Compute the modular multiplicative inverse of a modulo m.

    Returns x such that (a * x) % m == 1.
    Raises ValueError if no inverse exists.
    """
    g, x, _ = egcd(a, m)
    if g != 1:
        raise ValueError(f"No modular inverse exists for {a} modulo {m}")
    return x % m


def sqrt_mod(a: int, n: int) -> int:
    """Compute a square root of a modulo an odd n (which may be composite).

    Returns r such that r^2 ≡ a (mod n), or raises ValueError
    if no square root exists.

    This function should handle:
    - Prime modulus
    - Odd prime power modulus
    - Composite modulus (using the Chinese Remainder Theorem)

    Note: For composite n, you will need to factor n. For this task,
    n will be small enough to factor by trial division.
    """
    raise NotImplementedError(
        "sqrt_mod is not yet implemented. "
        "Implement modular square roots for arbitrary modulus."
    )
