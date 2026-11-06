The file `/app/ntlib.py` contains a number theory library with a `sqrt_mod(a, n)` function that is broken — it raises `NotImplementedError`. The file `/app/ecc.py` is an elliptic curve library that imports `sqrt_mod` and uses it for point decompression.

Your task is to fix the `sqrt_mod` function in `/app/ntlib.py` so that it correctly computes square roots modulo an arbitrary odd modulus `n`.

The function should:
- Return an integer `r` such that `r² ≡ a (mod n)`.
- Raise `ValueError` if no square root exists modulo `n`.
- Handle the case `a ≡ 0 (mod n)` by returning 0.
- Work for any odd modulus `n` — prime, prime power, or composite.

For composite `n`, you will need to factor it (trial division is sufficient — `n` will be small enough), compute square roots modulo each prime power factor, and combine results using the Chinese Remainder Theorem. For prime moduli, you will need to implement the general algorithm for computing square roots (not just the simple case where p ≡ 3 mod 4).

The elliptic curve library in `ecc.py` must work correctly after your fix — specifically, `decompress_point` should correctly recover the full (x, y) point from an x-coordinate and y parity bit.

You may use the helper functions already provided in `ntlib.py` (`gcd`, `egcd`, `modinv`). You may add additional helper functions to `ntlib.py` as needed. Do not modify `ecc.py`.