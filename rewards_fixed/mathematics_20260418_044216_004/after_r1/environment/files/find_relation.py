#!/usr/bin/env python3
"""Find and save an integer relation among the supplied constants."""

from math import gcd
from mpmath import mp

from constants import get_constants

mp.dps = 200


def pslq(values, tolerance=None, max_iterations=10000):
    """Return a nonzero integer relation for values, or None."""
    raise NotImplementedError("Implement PSLQ here")


def normalize(coefficients):
    coefficients = [int(value) for value in coefficients]
    divisor = 0
    for value in coefficients:
        divisor = gcd(divisor, abs(value))
    if divisor == 0:
        raise ValueError("the relation must be nonzero")
    coefficients = [value // divisor for value in coefficients]
    if next(value for value in coefficients if value) < 0:
        coefficients = [-value for value in coefficients]
    return coefficients


def main():
    values = get_constants()
    coefficients = pslq(values)
    if coefficients is None or len(coefficients) != len(values):
        raise RuntimeError("PSLQ did not return one coefficient per value")
    coefficients = normalize(coefficients)
    residual = mp.fsum(coefficient * value for coefficient, value in zip(coefficients, values))
    if abs(residual) >= mp.mpf("1e-100"):
        raise RuntimeError(f"candidate relation residual is too large: {residual}")
    with open("/app/relation.txt", "w", encoding="utf-8") as output:
        output.writelines(f"{coefficient}\n" for coefficient in coefficients)


if __name__ == "__main__":
    main()
