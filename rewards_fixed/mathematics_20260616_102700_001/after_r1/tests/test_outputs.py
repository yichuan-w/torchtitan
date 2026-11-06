"""Tests for sqrt_mod implementation and elliptic curve point decompression."""

import hashlib
import sys
sys.path.insert(0, "/app")


def test_sqrt_mod_zero():
    """sqrt_mod(0, n) should return 0 for any modulus."""
    from ntlib import sqrt_mod
    for n in [7, 97, 49, 91, 9973]:
        assert sqrt_mod(0, n) == 0, f"sqrt_mod(0, {n}) should be 0"


def test_sqrt_mod_prime_simple():
    """Test sqrt_mod on primes where p ≡ 3 (mod 4)."""
    from ntlib import sqrt_mod

    p = 7
    residues = {1: {1, 6}, 2: {3, 4}, 4: {2, 5}}
    for a, expected_roots in residues.items():
        r = sqrt_mod(a, p)
        assert (r * r) % p == a % p, f"sqrt_mod({a}, {p}) = {r}, but {r}^2 = {(r*r)%p} ≠ {a} mod {p}"
        assert r in expected_roots, f"sqrt_mod({a}, {p}) = {r}, expected one of {expected_roots}"

    p = 11
    residues = {1: {1, 10}, 3: {5, 6}, 4: {2, 9}, 5: {4, 7}, 9: {3, 8}}
    for a, expected_roots in residues.items():
        r = sqrt_mod(a, p)
        assert (r * r) % p == a % p, f"sqrt_mod({a}, {p}) = {r}, but {r}^2 = {(r*r)%p} ≠ {a} mod {p}"
        assert r in expected_roots, f"sqrt_mod({a}, {p}) = {r}, expected one of {expected_roots}"


def test_sqrt_mod_prime_general():
    """Test sqrt_mod on primes where p ≡ 1 (mod 4), requiring the general algorithm."""
    from ntlib import sqrt_mod

    p = 97
    for a in [4, 9, 16, 25, 36, 49, 53, 61, 64, 81]:
        r = sqrt_mod(a, p)
        assert (r * r) % p == a % p, (
            f"sqrt_mod({a}, {p}) = {r}, but {r}^2 = {(r*r)%p} ≠ {a} mod {p}"
        )
        assert 0 <= r < p, f"sqrt_mod({a}, {p}) = {r} is out of range [0, {p-1}]"

    p = 101
    for a in [4, 9, 16, 25, 36, 49, 64, 81, 100, 5, 14, 20, 30, 37]:
        r = sqrt_mod(a, p)
        assert (r * r) % p == a % p, (
            f"sqrt_mod({a}, {p}) = {r}, but {r}^2 = {(r*r)%p} ≠ {a} mod {p}"
        )
        assert 0 <= r < p, f"sqrt_mod({a}, {p}) = {r} is out of range [0, {p-1}]"


def test_sqrt_mod_prime_power():
    """Test sqrt_mod on prime power moduli (requires Hensel lifting)."""
    from ntlib import sqrt_mod

    # 7^2 = 49
    n = 49
    for a in [0, 1, 4, 9, 16, 25, 36, 2, 8, 11, 15, 18, 22, 23, 29, 30, 32, 37, 39, 44, 46]:
        r = sqrt_mod(a, n)
        assert (r * r) % n == a % n, (
            f"sqrt_mod({a}, {n}) = {r}, but {r}^2 = {(r*r)%n} ≠ {a} mod {n}"
        )
        assert 0 <= r < n, f"sqrt_mod({a}, {n}) = {r} is out of range [0, {n-1}]"

    # 3^3 = 27
    n = 27
    for a in [1, 4, 7, 9, 10, 13, 16, 19, 22, 25]:
        r = sqrt_mod(a, n)
        assert (r * r) % n == a % n, (
            f"sqrt_mod({a}, {n}) = {r}, but {r}^2 = {(r*r)%n} ≠ {a} mod {n}"
        )

    # 5^2 = 25
    n = 25
    for a in [1, 4, 6, 9, 11, 14, 16, 19, 21, 24]:
        r = sqrt_mod(a, n)
        assert (r * r) % n == a % n, (
            f"sqrt_mod({a}, {n}) = {r}, but {r}^2 = {(r*r)%n} ≠ {a} mod {n}"
        )

    # Tricky case: a is divisible by p
    # 7^3 = 343, test a = 49 = 7^2 (even exponent, should work)
    n = 343
    r = sqrt_mod(49, n)
    assert (r * r) % n == 49, f"sqrt_mod(49, {n}) = {r}, but {r}^2 % {n} = {(r*r)%n}"
    # 7^3 = 343, a = 14 = 2*7 (odd exponent, should fail)
    try:
        sqrt_mod(14, n)
        assert False, "sqrt_mod(14, 343) should raise ValueError (odd exponent)"
    except ValueError:
        pass

    # 7^3 = 343, a = 0 (should return 0)
    assert sqrt_mod(0, n) == 0


def test_sqrt_mod_composite():
    """Test sqrt_mod on composite moduli (requires CRT)."""
    from ntlib import sqrt_mod

    # 7 * 11 = 77
    n = 77
    for a in [1, 4, 9, 11, 14, 15, 16, 22, 23, 25, 36, 37, 42, 44, 49, 53, 56, 58, 60, 64, 67, 70, 71]:
        r = sqrt_mod(a, n)
        assert (r * r) % n == a % n, (
            f"sqrt_mod({a}, {n}) = {r}, but {r}^2 = {(r*r)%n} ≠ {a} mod {n}"
        )
        assert 0 <= r < n, f"sqrt_mod({a}, {n}) = {r} is out of range [0, {n-1}]"

    # 7 * 13 = 91
    n = 91
    for a in [1, 4, 9, 14, 16, 22, 23, 25, 29, 30, 35, 36, 39, 42, 43, 49, 51, 53, 56, 64, 65, 74, 77, 78, 79, 81, 88]:
        r = sqrt_mod(a, n)
        assert (r * r) % n == a % n, (
            f"sqrt_mod({a}, {n}) = {r}, but {r}^2 = {(r*r)%n} ≠ {a} mod {n}"
        )
        assert 0 <= r < n, f"sqrt_mod({a}, {n}) = {r} is out of range [0, {n-1}]"

def test_sqrt_mod_non_residue():
    """sqrt_mod should raise ValueError for non-quadratic residues."""
    from ntlib import sqrt_mod

    # Prime case
    p = 7
    for a in [3, 5, 6]:
        try:
            sqrt_mod(a, p)
            assert False, f"sqrt_mod({a}, {p}) should have raised ValueError"
        except ValueError:
            pass
        except NotImplementedError:
            assert False, f"sqrt_mod still raises NotImplementedError"

    # Prime power case
    n = 49
    for a in [3, 5, 6, 10, 12, 13, 17, 19, 20, 24, 26, 27, 31, 33, 34, 38, 40, 41, 45, 47, 48]:
        try:
            sqrt_mod(a, n)
            assert False, f"sqrt_mod({a}, {n}) should have raised ValueError"
        except ValueError:
            pass
        except NotImplementedError:
            assert False, f"sqrt_mod still raises NotImplementedError"

    # Composite case
    n = 77
    for a in [2, 3, 5, 6, 8, 10, 12, 13, 17, 18, 19, 20, 21, 24, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 38, 39, 40, 41, 43, 45, 46, 47, 48, 50, 51, 52, 54, 55, 57, 59, 61, 62, 63, 65, 66, 68, 69, 72, 73, 74, 75, 76]:
        try:
            sqrt_mod(a, n)
            assert False, f"sqrt_mod({a}, {n}) should have raised ValueError"
        except ValueError:
            pass
        except NotImplementedError:
            assert False, f"sqrt_mod still raises NotImplementedError"


def test_ecc_decompress_small_curve():
    """Test that elliptic curve point decompression works with sqrt_mod."""
    from ecc import EllipticCurve
    from ntlib import sqrt_mod

    curve = EllipticCurve(a=2, b=3, p=97)

    for x in range(97):
        y_sq = (pow(x, 3, 97) + 2 * x + 3) % 97
        try:
            y = sqrt_mod(y_sq, 97)
        except ValueError:
            continue
        parity = y % 2
        dx, dy = curve.decompress_point(x, parity)
        assert dx == x, f"decompress_point({x}, {parity}) returned x={dx}, expected {x}"
        assert dy == y or dy == 97 - y, (
            f"decompress_point({x}, {parity}) returned y={dy}, expected {y} or {97-y}"
        )
        assert dy % 2 == parity, (
            f"decompress_point({x}, {parity}) returned y={dy} with parity {dy%2}"
        )
        assert curve.is_on_curve(dx, dy), f"Point ({dx}, {dy}) is not on the curve"
        break


def test_ecc_point_operations():
    """Test that the full elliptic curve library works with sqrt_mod."""
    from ecc import EllipticCurve
    from ntlib import sqrt_mod

    curve = EllipticCurve(a=2, b=3, p=97)

    x = 1
    y_sq = (pow(x, 3, 97) + 2 * x + 3) % 97
    while True:
        try:
            y = sqrt_mod(y_sq, 97)
            break
        except ValueError:
            x += 1
            y_sq = (pow(x, 3, 97) + 2 * x + 3) % 97

    P = (x, y)
    assert curve.is_on_curve(*P), f"Point P={P} should be on the curve"

    P2 = curve.point_add(P, P)
    assert curve.is_on_curve(*P2), f"2P={P2} should be on the curve"

    P3 = curve.scalar_mult(3, P)
    assert curve.is_on_curve(*P3), f"3P={P3} should be on the curve"

    P3_alt = curve.point_add(P, P2)
    assert P3 == P3_alt, f"3P computed two ways: {P3} vs {P3_alt}"

    P0 = curve.scalar_mult(0, P)
    assert P0 == (None, None), f"0*P should be point at infinity, got {P0}"

    P_neg = curve.point_add(P, (P[0], (-P[1]) % 97))
    assert P_neg == (None, None), f"P + (-P) should be point at infinity, got {P_neg}"


def test_sqrt_mod_not_implemented_error_gone():
    """Verify that sqrt_mod no longer raises NotImplementedError."""
    from ntlib import sqrt_mod

    try:
        sqrt_mod(4, 7)
    except NotImplementedError:
        assert False, "sqrt_mod still raises NotImplementedError - it must be implemented!"
    except ValueError:
        pass
    except Exception:
        pass


def test_ecc_unmodified():
    """The shipped elliptic curve library must remain byte-for-byte unchanged."""
    with open("/app/ecc.py", "rb") as ecc_file:
        digest = hashlib.sha256(ecc_file.read()).hexdigest()
    assert digest == "ea7b104fa30daf0e5ba2a5ae472808a8da17e4c295115c690231faeedceb6e43"
