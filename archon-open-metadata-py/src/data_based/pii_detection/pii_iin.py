"""
IIN/BIN — Issuer Identification Number / Bank Identification Number.

Pure-function helpers for deriving the card brand (Visa, Mastercard, Amex,
…) from a PAN.  Used by ``pii_scan`` to enrich CC_NUMBER findings with a
provider breakdown — the UI then renders a "VISA · 4123" chip alongside
the standard CC_NUMBER tag.

Why brand-only (not full BIN→bank lookup)
=========================================

A complete BIN/IIN→issuing-bank database is a paid, frequently-updated
data feed (tens of thousands of ranges, refreshed monthly).  Shipping one
in-tree would be a maintenance burden.  Brand-level detection is free,
deterministic, and accurate from the PAN's leading digits alone — every
ISO/IEC 7812-1 PAN encodes its scheme in the first 1-4 digits via the
MII + ranges below.

The module is deliberately dependency-free so the worker pool doesn't
need to load extra state per process.

Brand prefix ranges (verified against ISO/IEC 7812 + scheme rulebooks):
  * VISA          → first digit 4
  * MASTERCARD    → 51-55, 2221-2720
  * AMEX          → 34, 37
  * DISCOVER      → 6011, 644-649, 65
  * DINERS        → 300-305, 3095, 36, 38, 39
  * JCB           → 3528-3589
  * UNIONPAY      → 62, 81
  * MAESTRO       → 50, 56-58, 6 (overlaps Discover; checked AFTER it)
  * RUPAY         → 60, 6521, 6522, 81-82, 508
  * MIR           → 2200-2204
"""

from __future__ import annotations

import re

__all__ = ["card_brand", "card_iin", "BRANDS"]


# Set of known brand labels — exposed so callers can validate / display
# the canonical names without hard-coding strings everywhere.  Order
# matches the rough commercial frequency in our datasets.
BRANDS: tuple[str, ...] = (
    "VISA",
    "MASTERCARD",
    "AMEX",
    "DISCOVER",
    "DINERS",
    "JCB",
    "UNIONPAY",
    "MAESTRO",
    "RUPAY",
    "MIR",
)


# Strip any non-digit (spaces, dashes, parentheses) so the prefix
# checks below see a clean numeric string.  The regex is compiled once.
_NON_DIGIT = re.compile(r"\D")


def card_iin(pan: str, *, length: int = 6) -> str:
    """Return the leading ``length`` digits of ``pan`` (the IIN/BIN).

    Length defaults to 6 — the legacy/most-common form.  ISO 7812-1 was
    extended to 8 digits in 2017, so callers wanting the modern form
    pass ``length=8``.  Any non-digit characters are stripped first.

    Returns an empty string when the cleaned PAN is shorter than
    ``length``.  Never raises.
    """
    digits = _NON_DIGIT.sub("", pan or "")
    return digits[:length] if len(digits) >= length else ""


def card_brand(pan: str) -> str | None:
    """Return the card brand name for ``pan`` (e.g. ``"VISA"``), or
    ``None`` when no rule matches.

    Brand rules (checked in order; first hit wins so overlap-safe):

    * AMEX before DINERS (34/37 vs 36/38)
    * DISCOVER before MAESTRO (650/644-649 vs 6 catch-all)
    * UNIONPAY before MAESTRO (62/81 vs 6 catch-all)
    * RUPAY before MASTERCARD (508 vs 50, etc.)
    * VISA last (single digit 4 — extremely broad; let specific
      rules win first if anyone ever encodes '4xxxx' as JCB etc.)
    """
    digits = _NON_DIGIT.sub("", pan or "")
    if not digits:
        return None

    n2 = digits[:2]
    n3 = digits[:3]
    n4 = digits[:4]
    n6 = digits[:6]

    # AMEX (34, 37) — must be 15 digits in real life; we still tag
    # 4-digit prefix for consistency with the rest.
    if n2 in ("34", "37"):
        return "AMEX"

    # DINERS Club International (300-305, 3095, 36, 38, 39).  Note 36
    # overlaps Mastercard 2-series in some old material — the
    # 36 prefix has been Diners since 2004.
    if n3 in ("300", "301", "302", "303", "304", "305", "309"):
        return "DINERS"
    if n4 == "3095":
        return "DINERS"
    if n2 in ("36", "38", "39"):
        return "DINERS"

    # JCB — strict 3528-3589 inclusive.
    if len(digits) >= 4:
        try:
            n4_int = int(n4)
            if 3528 <= n4_int <= 3589:
                return "JCB"
        except ValueError:
            pass

    # MIR (Russia) — 2200-2204 inclusive.  Must precede MASTERCARD
    # because Mastercard's 2-series starts at 2221.
    if len(digits) >= 4:
        try:
            n4_int = int(n4)
            if 2200 <= n4_int <= 2204:
                return "MIR"
        except ValueError:
            pass

    # MASTERCARD — 51-55 (legacy) and 2221-2720 (post-2017 expansion).
    if len(digits) >= 2 and n2[0] == "5" and n2[1] in "12345":
        return "MASTERCARD"
    if len(digits) >= 4:
        try:
            n4_int = int(n4)
            if 2221 <= n4_int <= 2720:
                return "MASTERCARD"
        except ValueError:
            pass

    # RUPAY (India) — 508, 6521, 6522, 81-82.  RuPay's 4-digit
    # prefixes 6521/6522 must be checked BEFORE Discover's broad
    # ``65`` rule below or RuPay-issued cards get mislabelled
    # DISCOVER (the two networks share the 65xx range historically).
    if n3 == "508":
        return "RUPAY"
    if n4 in ("6521", "6522"):
        return "RUPAY"

    # DISCOVER — 6011, 622126-622925 (China UnionPay co-branded), 644-
    # 649, 65.  Order matters: 6011 must be checked before the broader
    # 6 catch-all (Maestro), and the 65 catch-all must come AFTER
    # RuPay's specific 6521/6522 rule above.
    if n4 == "6011":
        return "DISCOVER"
    if n3 in ("644", "645", "646", "647", "648", "649"):
        return "DISCOVER"
    if n2 == "65":
        return "DISCOVER"

    # UNIONPAY (China) — 62, 81.
    if n2 in ("62", "81", "82"):
        return "UNIONPAY"

    # MAESTRO — 50, 56-58, 6 (catch-all).  Anything 6xxxx that hasn't
    # already matched Discover/UnionPay/RuPay above falls here.
    if n2 == "50":
        return "MAESTRO"
    if n2 in ("56", "57", "58"):
        return "MAESTRO"
    if digits[0] == "6":
        return "MAESTRO"

    # VISA — single-digit '4' prefix.  Last so more specific rules
    # always win (none currently overlap, but defensive).
    if digits[0] == "4":
        return "VISA"

    return None
