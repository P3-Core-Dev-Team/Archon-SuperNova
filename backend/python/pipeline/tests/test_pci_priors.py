"""Tests for the PCI cardholder-data priors (CARD_HOLDER_NAME, CARD_CVV).

Existing CC_NUMBER prior coverage already lives in the broader
test_priors corpus; these tests exercise the two newly-added types
plus the regulation-tag plumbing so a future refactor can't silently
drop the "PCI" classification that the UI groups by.
"""

from __future__ import annotations

import pytest

from discovery.pii_priors import name_prior, name_prior_strength
from discovery.pii_patterns import get_pattern


# --------------------------------------------------------------------- #
# CARD_HOLDER_NAME prior
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("col", [
    "card_holder",
    "card_holder_name",
    "cardholder",
    "name_on_card",
    "cc_holder",
    "CARD_HOLDER",  # case-insensitive
    "CardHolderName",  # camel-case (normalised before regex)
])
def test_card_holder_name_prior_matches(col):
    assert name_prior(col) == "CARD_HOLDER_NAME"
    # Strength must be >0 so the scanner accepts a name-prior-only
    # finding when no value-shape hits the regex.
    assert name_prior_strength(col, "CARD_HOLDER_NAME") > 0.0


@pytest.mark.parametrize("col", [
    "first_name",
    "customer_name",
    "patient_name",
])
def test_generic_name_columns_do_not_become_card_holder(col):
    assert name_prior(col) == "PERSON_NAME"


# --------------------------------------------------------------------- #
# CARD_CVV prior
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("col", [
    "cvv",
    "cvv2",
    "cvc",
    "csc",
    "card_security_code",
    "card_verification",
    "card_verification_value",
    "security_code",
    "verification_value",
    "CVV",
])
def test_card_cvv_prior_matches(col):
    assert name_prior(col) == "CARD_CVV"
    assert name_prior_strength(col, "CARD_CVV") > 0.0


@pytest.mark.parametrize("col", [
    # Three-digit ambiguous columns that should NOT light up CARD_CVV
    # via the prior: the prior is name-based, not shape-based.
    "country_code",
    "currency_code",
    "language_code",
    "status_code",
])
def test_unrelated_code_columns_do_not_match_cvv(col):
    assert name_prior(col) != "CARD_CVV"


# --------------------------------------------------------------------- #
# Regulation tags carry through to the PatternDef catalog
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("pii_type, expected_reg", [
    ("CC_NUMBER", "PCI"),
    ("CARD_HOLDER_NAME", "PCI"),
    ("CARD_CVV", "PCI"),
])
def test_pci_pattern_tagged_with_pci_regulation(pii_type, expected_reg):
    p = get_pattern(pii_type)
    assert p is not None, f"pattern {pii_type} not registered"
    assert expected_reg in p.regulated, (
        f"{pii_type}.regulated={p.regulated} missing {expected_reg!r}"
    )


def test_card_holder_name_also_tagged_gdpr():
    """Cardholder name is BOTH PCI (cardholder data) and GDPR (PII)."""
    p = get_pattern("CARD_HOLDER_NAME")
    assert p is not None
    assert "PCI" in p.regulated
    assert "GDPR" in p.regulated


# --------------------------------------------------------------------- #
# Negative: unrelated columns don't accidentally PCI-tag
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("col", [
    "first_name",        # PERSON_NAME — no PCI
    "phone_number",      # PHONE — no PCI
    "email",             # EMAIL — GDPR / CCPA, no PCI
    "country_code",      # COUNTRY_CODE — no PCI
])
def test_non_pci_columns_do_not_get_pci_priors(col):
    t = name_prior(col)
    if t is None:
        return
    p = get_pattern(t)
    if p is not None:
        assert "PCI" not in p.regulated
