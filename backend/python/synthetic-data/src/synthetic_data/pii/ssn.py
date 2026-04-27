"""US Social Security Number generator.

Avoids reserved area numbers: 000, 666, 900-999.
"""

from __future__ import annotations

import random as stdlib_random


# Invalid area numbers per SSA rules
_INVALID_AREAS = frozenset([0, 666] + list(range(900, 1000)))


def _valid_area(rng: stdlib_random.Random) -> int:
    """Pick a valid SSN area number (1-899 excluding 666)."""
    while True:
        area = rng.randint(1, 899)
        if area not in _INVALID_AREAS:
            return area


def generate_ssn(rng: stdlib_random.Random | None = None) -> str:
    """
    Generate a single realistic US SSN string in AAA-GG-SSSS format.
    Avoids 000, 666, and 900-999 area numbers.
    """
    if rng is None:
        rng = stdlib_random.Random()
    area = _valid_area(rng)
    group = rng.randint(1, 99)
    serial = rng.randint(1, 9999)
    return f"{area:03d}-{group:02d}-{serial:04d}"


def generate_ssn_batch(n: int, rng: stdlib_random.Random | None = None) -> list[str]:
    """Generate n SSNs."""
    if rng is None:
        rng = stdlib_random.Random()
    return [generate_ssn(rng) for _ in range(n)]


def fake_ssn_999(rng: stdlib_random.Random | None = None) -> str:
    """
    Generate an SSN-shaped string with 999 area prefix — looks like SSN but is NOT valid.
    Used for employee_id noise values.
    """
    if rng is None:
        rng = stdlib_random.Random()
    group = rng.randint(10, 99)
    serial = rng.randint(1000, 9999)
    return f"999-{group:02d}-{serial:04d}"


def fake_ssn_999_batch(n: int, rng: stdlib_random.Random | None = None) -> list[str]:
    """Generate n SSN-shaped strings with invalid 999 prefix."""
    if rng is None:
        rng = stdlib_random.Random()
    return [fake_ssn_999(rng) for _ in range(n)]
