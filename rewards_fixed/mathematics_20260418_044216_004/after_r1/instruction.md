I have a Python module `constants.py` that provides 6 high-precision real numbers via `get_constants()`. These constants satisfy an integer relation: there exist integers a0, a1, ..., a5 (not all zero) such that a0*x0 + a1*x1 + ... + a5*x5 = 0.

Complete the starter program `/app/find_relation.py` so that it discovers this integer relation using the PSLQ algorithm and saves the coefficients to `/app/relation.txt`. The starter already handles normalization, validation, and file output; implement its `pslq()` function. The file should contain one integer per line, representing the coefficients [a0, a1, a2, a3, a4, a5] in order.

Requirements:
- You must implement the PSLQ algorithm yourself (do not use mpmath.pslq or similar library functions)
- Use the mpmath library for high-precision arithmetic (the constants are computed to 200-digit precision)
- The output should be normalized so that the gcd of all coefficients is 1, and the first non-zero coefficient is positive

