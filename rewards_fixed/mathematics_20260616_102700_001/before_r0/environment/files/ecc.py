"""
Elliptic curve cryptography module.

Implements elliptic curve arithmetic over prime fields and provides
point decompression from compact representation.
"""

from ntlib import sqrt_mod, modinv


class EllipticCurve:
    """An elliptic curve y^2 = x^3 + ax + b over F_p."""

    def __init__(self, a: int, b: int, p: int):
        self.a = a % p
        self.b = b % p
        self.p = p

    def is_on_curve(self, x: int, y: int) -> bool:
        """Check if the point (x, y) lies on the curve."""
        if x is None and y is None:
            return True  # point at infinity
        left = (y * y) % self.p
        right = (pow(x, 3, self.p) + self.a * x + self.b) % self.p
        return left == right

    def decompress_point(self, x: int, y_parity: int) -> tuple:
        """Recover the full point (x, y) from x-coordinate and y parity.

        Args:
            x: The x-coordinate of the point.
            y_parity: 0 for even y, 1 for odd y.

        Returns:
            (x, y) tuple representing the point.

        Raises:
            ValueError: If x is not a valid x-coordinate on the curve.
        """
        y_sq = (pow(x, 3, self.p) + self.a * x + self.b) % self.p
        y = sqrt_mod(y_sq, self.p)
        if y % 2 != y_parity:
            y = self.p - y
        return (x, y)

    def point_add(self, P: tuple, Q: tuple) -> tuple:
        """Add two points on the curve.

        Handles point at infinity (represented as (None, None)).
        """
        if P == (None, None):
            return Q
        if Q == (None, None):
            return P

        x1, y1 = P
        x2, y2 = Q

        if x1 == x2:
            if (y1 + y2) % self.p == 0:
                return (None, None)  # P + (-P) = point at infinity
            # P == Q, point doubling
            return self._point_double(P)

        # Point addition: P != Q, x1 != x2
        lam = ((y2 - y1) * modinv(x2 - x1, self.p)) % self.p
        x3 = (lam * lam - x1 - x2) % self.p
        y3 = (lam * (x1 - x3) - y1) % self.p
        return (x3, y3)

    def _point_double(self, P: tuple) -> tuple:
        """Double a point on the curve."""
        if P == (None, None):
            return (None, None)

        x1, y1 = P
        if y1 == 0:
            return (None, None)

        lam = ((3 * x1 * x1 + self.a) * modinv(2 * y1, self.p)) % self.p
        x3 = (lam * lam - 2 * x1) % self.p
        y3 = (lam * (x1 - x3) - y1) % self.p
        return (x3, y3)

    def scalar_mult(self, k: int, P: tuple) -> tuple:
        """Compute k * P using double-and-add."""
        if k == 0 or P == (None, None):
            return (None, None)
        if k < 0:
            P = (P[0], (-P[1]) % self.p)
            k = -k

        result = (None, None)
        addend = P

        while k:
            if k & 1:
                result = self.point_add(result, addend)
            addend = self.point_add(addend, addend)
            k >>= 1

        return result

    def point_order(self, P: tuple) -> int:
        """Compute the order of point P (naive method for small orders)."""
        if P == (None, None):
            return 1
        Q = P
        order = 1
        while Q != (None, None):
            Q = self.point_add(Q, P)
            order += 1
            if order > 1000000:
                raise ValueError("Order too large to compute naively")
        return order


def make_curve(curve_name: str = "test") -> EllipticCurve:
    """Create a standard elliptic curve.

    Currently supports 'test'.
    """
    if curve_name == "test":
        # A small test curve: y^2 = x^3 + 2x + 3 over F_97
        p = 97
        a = 2
        b = 3
    else:
        raise ValueError(f"Unknown curve: {curve_name}")
    return EllipticCurve(a, b, p)