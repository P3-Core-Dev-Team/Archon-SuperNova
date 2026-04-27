"""Luhn-valid credit card number generator."""

from __future__ import annotations

import random as stdlib_random


# Prefixes: (prefix_string, total_card_length)
CARD_PREFIXES: list[tuple[str, int]] = [
    ("4", 16),      # Visa
    ("51", 16),     # Mastercard
    ("52", 16),
    ("53", 16),
    ("54", 16),
    ("55", 16),
    ("34", 15),     # Amex
    ("37", 15),
]


def _luhn_checksum(digits: list[int]) -> int:
    """Compute Luhn check digit for a list of digits (without check digit)."""
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d2 = d * 2
            total += d2 - 9 if d2 > 9 else d2
        else:
            total += d
    return (10 - (total % 10)) % 10


def generate_luhn(
    prefix_list: list[tuple[str, int]] | None = None,
    rng: stdlib_random.Random | None = None,
) -> str:
    """
    Generate a single Luhn-valid card number.

    Parameters
    ----------
    prefix_list : list of (prefix, total_length) tuples. Defaults to Visa/MC/Amex.
    rng         : seeded stdlib random.Random for determinism

    Returns
    -------
    card number as string (digits only, no spaces)
    """
    if prefix_list is None:
        prefix_list = CARD_PREFIXES
    if rng is None:
        rng = stdlib_random.Random()

    prefix_str, length = rng.choice(prefix_list)
    prefix_digits = [int(c) for c in prefix_str]
    n_random = length - len(prefix_digits) - 1  # -1 for check digit
    random_digits = [rng.randint(0, 9) for _ in range(n_random)]
    all_digits = prefix_digits + random_digits
    check = _luhn_checksum(all_digits)
    return "".join(str(d) for d in all_digits) + str(check)


def generate_luhn_batch(
    n: int,
    prefix_list: list[tuple[str, int]] | None = None,
    rng: stdlib_random.Random | None = None,
) -> list[str]:
    """Generate n Luhn-valid card numbers."""
    if rng is None:
        rng = stdlib_random.Random()
    return [generate_luhn(prefix_list=prefix_list, rng=rng) for _ in range(n)]


def fake_cc_failing_luhn(
    n: int,
    rng: stdlib_random.Random | None = None,
) -> list[str]:
    """
    Generate n credit-card-shaped numbers that intentionally fail Luhn.
    Used for tracking numbers that look like CC but should NOT be detected.
    """
    if rng is None:
        rng = stdlib_random.Random()
    results = []
    while len(results) < n:
        # Generate a real Luhn number then corrupt the check digit
        card = generate_luhn(rng=rng)
        digits = list(card)
        check = int(digits[-1])
        bad_check = (check + rng.randint(1, 9)) % 10  # different digit
        digits[-1] = str(bad_check)
        results.append("".join(digits))
    return results


def luhn_valid(card_number: str) -> bool:
    """Return True if card_number passes the Luhn algorithm."""
    digits = [int(c) for c in card_number if c.isdigit()]
    if len(digits) < 2:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d2 = d * 2
            total += d2 - 9 if d2 > 9 else d2
        else:
            total += d
    return total % 10 == 0
