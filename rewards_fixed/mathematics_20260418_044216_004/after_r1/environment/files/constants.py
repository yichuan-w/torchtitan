"""High-precision constants for the integer-relation task."""

from mpmath import mp

mp.dps = 200


def get_constants():
    """Return six 200-digit real numbers with an integer relation."""
    return [
        mp.sqrt(2),
        mp.sqrt(3),
        mp.sqrt(5),
        mp.sqrt(7),
        mp.sqrt(11),
        mp.mpf("1.967791019763863478276913086877306197873398155620551629592226696305538721301519992489666921694257960688231493338295936215864741684923396342286413440525787914543300131412257293717510334080174291730683016188"),
    ]


def get_precision():
    return mp.dps
