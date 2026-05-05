"""Unit tests for ``discovery.pii_priors``.

Per the upgrade plan: column-name → PII type priors must catch the obvious
cases (``ssn`` → SSN_US, ``email_address`` → EMAIL) and ignore unrelated
columns.  ``name_prior_strength`` returns the Bayesian π_name component
used by ``column_pii_confidence``.
"""
from __future__ import annotations

import pytest

from discovery.pii_priors import (
    COLUMN_NAME_PRIORS,
    name_prior,
    name_prior_strength,
)


# ---------------------------------------------------------------------------
# name_prior — exact and substring keys
# ---------------------------------------------------------------------------


class TestNamePrior:
    def test_ssn_exact(self) -> None:
        assert name_prior("ssn") == "SSN_US"

    def test_social_security_underscore(self) -> None:
        assert name_prior("social_security") == "SSN_US"

    def test_email_address(self) -> None:
        assert name_prior("email_address") == "EMAIL"

    def test_email_short(self) -> None:
        assert name_prior("email") == "EMAIL"

    def test_e_mail_underscore_variant(self) -> None:
        assert name_prior("e_mail") == "EMAIL"

    def test_dob(self) -> None:
        assert name_prior("dob") == "DOB"

    def test_date_of_birth(self) -> None:
        assert name_prior("date_of_birth") == "DOB"

    def test_birthday(self) -> None:
        assert name_prior("birthday") == "DOB"

    def test_passport_no(self) -> None:
        assert name_prior("passport_no") == "PASSPORT_US"

    def test_iban(self) -> None:
        assert name_prior("iban") == "IBAN"

    def test_aadhaar(self) -> None:
        assert name_prior("aadhaar") == "AADHAAR_IN"

    def test_phone_variant_msisdn(self) -> None:
        assert name_prior("msisdn") == "PHONE"

    def test_zipcode(self) -> None:
        assert name_prior("zipcode") == "POSTAL_CODE"

    def test_first_name(self) -> None:
        assert name_prior("first_name") == "PERSON_NAME"

    def test_address(self) -> None:
        assert name_prior("address") == "ADDRESS"

    def test_unknown_column(self) -> None:
        assert name_prior("foo_bar_baz") is None

    def test_empty_string(self) -> None:
        assert name_prior("") is None

    def test_underscore_only(self) -> None:
        assert name_prior("___") is None


# ---------------------------------------------------------------------------
# name_prior_strength — exact = 0.85, substring = 0.50, miss = 0.0
# ---------------------------------------------------------------------------


class TestNamePriorStrength:
    def test_exact_match_strong(self) -> None:
        # 'ssn' is an exact key for SSN_US.
        assert name_prior_strength("ssn", "SSN_US") == pytest.approx(0.85)

    def test_substring_match_medium(self) -> None:
        # 'employee_ssn' contains 'ssn' but isn't equal to it.
        assert name_prior_strength("employee_ssn", "SSN_US") == pytest.approx(0.50)

    def test_wrong_pii_type_zero(self) -> None:
        # 'ssn' should yield SSN_US prior, not EMAIL.
        assert name_prior_strength("ssn", "EMAIL") == 0.0

    def test_unknown_column_zero(self) -> None:
        assert name_prior_strength("xyz", "SSN_US") == 0.0

    def test_empty_returns_zero(self) -> None:
        assert name_prior_strength("", "SSN_US") == 0.0

    def test_email_address_substring(self) -> None:
        # 'customer_email_address' contains 'email_address' but isn't equal
        # to one of the EMAIL keys.
        s = name_prior_strength("customer_email_address", "EMAIL")
        assert s == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# Sanity: every value in the prior table is a non-empty PII type label
# ---------------------------------------------------------------------------


def test_all_prior_values_nonempty() -> None:
    for pat, pii_type in COLUMN_NAME_PRIORS.items():
        assert isinstance(pii_type, str) and pii_type, pat
