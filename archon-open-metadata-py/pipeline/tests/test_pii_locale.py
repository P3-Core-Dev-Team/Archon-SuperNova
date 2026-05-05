"""Unit tests for ``discovery.pii_locale``.

Locale-aware validators are skipped if the underlying library
(``phonenumbers``, specific ``stdnum`` sub-modules) isn't installed in the
test env — the production code falls back to the regex matchers in those
cases, and the tests document that contract.
"""
from __future__ import annotations

import importlib.util

import pytest

from discovery.pii_locale import (
    LOCALE_VALIDATORS,
    PHONENUMBERS_AVAILABLE,
    geo_coord_in_range,
    get_validator,
    ipv4_in_range,
    phone_valid,
)


# ---------------------------------------------------------------------------
# phone_valid — depends on `phonenumbers`
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not PHONENUMBERS_AVAILABLE,
    reason="phonenumbers library not installed",
)
class TestPhoneValid:
    def test_us_number_valid(self) -> None:
        # E.164 form + region hint
        assert phone_valid("+12125551234", default_region="US") is True

    def test_us_number_local_form(self) -> None:
        assert phone_valid("(212) 555-1234", default_region="US") is True

    def test_in_number(self) -> None:
        # Indian mobile — valid format
        assert phone_valid("+919876543210", default_region="IN") is True

    def test_gb_number(self) -> None:
        # Real UK number format
        assert phone_valid("+442071838750", default_region="GB") is True

    def test_garbage_input(self) -> None:
        assert phone_valid("not-a-number", default_region="US") is False

    def test_too_short(self) -> None:
        assert phone_valid("12", default_region="US") is False


@pytest.mark.skipif(
    PHONENUMBERS_AVAILABLE,
    reason="when phonenumbers IS installed, the permissive fallback is bypassed",
)
def test_phone_valid_permissive_when_lib_missing() -> None:
    """Without phonenumbers, ``phone_valid`` must be permissive (True)."""
    assert phone_valid("212-555-1234") is True


# ---------------------------------------------------------------------------
# ipv4_in_range — pure helper, always available
# ---------------------------------------------------------------------------


class TestIpv4InRange:
    def test_loopback(self) -> None:
        assert ipv4_in_range("127.0.0.1") is True

    def test_zero(self) -> None:
        assert ipv4_in_range("0.0.0.0") is True

    def test_max(self) -> None:
        assert ipv4_in_range("255.255.255.255") is True

    def test_overflow(self) -> None:
        assert ipv4_in_range("999.0.0.0") is False

    def test_negative(self) -> None:
        assert ipv4_in_range("-1.0.0.0") is False

    def test_wrong_octet_count(self) -> None:
        assert ipv4_in_range("1.2.3") is False
        assert ipv4_in_range("1.2.3.4.5") is False

    def test_non_numeric(self) -> None:
        assert ipv4_in_range("a.b.c.d") is False


# ---------------------------------------------------------------------------
# geo_coord_in_range
# ---------------------------------------------------------------------------


class TestGeoCoord:
    def test_valid_coord(self) -> None:
        assert geo_coord_in_range("37.7749,-122.4194") is True  # SF

    def test_out_of_range_lat(self) -> None:
        assert geo_coord_in_range("91.0,0.0") is False

    def test_out_of_range_lon(self) -> None:
        assert geo_coord_in_range("0.0,181.0") is False

    def test_malformed(self) -> None:
        assert geo_coord_in_range("not-a-coord") is False


# ---------------------------------------------------------------------------
# stdnum-backed validators — only present if the corresponding sub-module is
# importable.  Each test guards on importlib.util.find_spec.
# ---------------------------------------------------------------------------


def _has_stdnum(mod_path: str) -> bool:
    return importlib.util.find_spec(f"stdnum.{mod_path}") is not None


@pytest.mark.skipif(
    not _has_stdnum("in_.aadhaar"),
    reason="stdnum.in_.aadhaar not available",
)
def test_aadhaar_in_validator() -> None:
    fn = LOCALE_VALIDATORS["aadhaar_in"]
    # Synthetic Verhoeff-valid Aadhaar (commonly used as test value):
    # 234123412346 has correct mod-10 Verhoeff checksum.
    assert callable(fn)


@pytest.mark.skipif(
    not _has_stdnum("in_.pan"),
    reason="stdnum.in_.pan not available",
)
def test_pan_in_validator_format() -> None:
    fn = LOCALE_VALIDATORS["pan_in"]
    # Format ABCDE1234F: 5 letters + 4 digits + 1 letter.  stdnum requires
    # the 4th letter be a valid PAN entity-type — 'P' for individual.
    assert callable(fn)
    # A well-formed PAN should validate; a malformed one should not.
    assert fn("ABCPE1234F") is True
    assert fn("ABCDE12345") is False


@pytest.mark.skipif(
    not _has_stdnum("br.cpf"),
    reason="stdnum.br.cpf not available",
)
def test_cpf_br_validator() -> None:
    fn = LOCALE_VALIDATORS["cpf_br"]
    assert callable(fn)
    # Known invalid CPF (all zeros)
    assert fn("000.000.000-00") is False


# ---------------------------------------------------------------------------
# get_validator dispatch
# ---------------------------------------------------------------------------


def test_get_validator_unknown_returns_none() -> None:
    assert get_validator("does_not_exist") is None


def test_get_validator_iban_present() -> None:
    # iban is one of the most-likely-to-be-installed stdnum modules.
    fn = get_validator("iban")
    assert fn is not None
    assert callable(fn)


def test_get_validator_phone_present() -> None:
    fn = get_validator("phone")
    assert fn is phone_valid


def test_get_validator_ipv4_present() -> None:
    assert get_validator("ipv4") is ipv4_in_range
