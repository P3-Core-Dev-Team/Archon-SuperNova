"""Unit tests for the expanded PII pattern catalog (``discovery.pii_patterns``).

Per the upgrade plan: at least 3 test cases per high-priority new pattern
(passport, IBAN, IPv4, plus a sample of national IDs).
"""
from __future__ import annotations

import pytest

from discovery.pii_patterns import (
    PATTERNS,
    SPECIFICITY,
    PatternDef,
    get_pattern,
)
from discovery.pii_scan import PIIMatcher


@pytest.fixture(scope="module")
def matcher() -> PIIMatcher:
    return PIIMatcher()


def _has(matcher: PIIMatcher, text: str, name: str) -> bool:
    return any(hit[0] == name for hit in matcher.scan(text))


# ---------------------------------------------------------------------------
# Catalog sanity
# ---------------------------------------------------------------------------


class TestCatalogSanity:
    def test_count_grew(self) -> None:
        # 8 existing + 39 new
        assert len(PATTERNS) >= 47

    def test_unique_names(self) -> None:
        names = [p.name for p in PATTERNS]
        assert len(names) == len(set(names))

    def test_patterndef_frozen(self) -> None:
        p = PATTERNS[0]
        with pytest.raises(Exception):  # noqa: PT011  - frozen dataclass raises FrozenInstanceError
            p.name = "MUTATED"  # type: ignore[misc]

    def test_specificity_table_complete(self) -> None:
        # Every pattern in PATTERNS has an entry in SPECIFICITY.
        for p in PATTERNS:
            assert p.name in SPECIFICITY

    def test_get_pattern_roundtrip(self) -> None:
        assert get_pattern("EMAIL") is not None
        assert get_pattern("DOES_NOT_EXIST") is None


# ---------------------------------------------------------------------------
# PASSPORT_US — 1 letter + 8 digits, OR 2 letters + 7 digits
# ---------------------------------------------------------------------------


class TestPassportUS:
    def test_one_letter(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "A12345678", "PASSPORT_US")

    def test_two_letters(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "AB1234567", "PASSPORT_US")

    def test_lowercase_rejected(self, matcher: PIIMatcher) -> None:
        # Pattern is uppercase-only.
        assert not _has(matcher, "a12345678", "PASSPORT_US")

    def test_too_short(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "A123456", "PASSPORT_US")


# ---------------------------------------------------------------------------
# PASSPORT_IN — A-PR-WY + 1-9 + 6 digits
# ---------------------------------------------------------------------------


class TestPassportIN:
    def test_a_prefix(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "A1234567", "PASSPORT_IN")

    def test_w_prefix(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "W7654321", "PASSPORT_IN")

    def test_q_prefix_rejected(self, matcher: PIIMatcher) -> None:
        # 'Q' is excluded by the [A-PR-WY] character class.
        assert not _has(matcher, "Q1234567", "PASSPORT_IN")


# ---------------------------------------------------------------------------
# IPV4
# ---------------------------------------------------------------------------


class TestIPv4:
    def test_loopback(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "127.0.0.1", "IPV4")

    def test_routable(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "8.8.8.8", "IPV4")

    def test_max(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "255.255.255.255", "IPV4")

    def test_overflow_rejected(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "999.999.999.999", "IPV4")

    def test_partial_rejected(self, matcher: PIIMatcher) -> None:
        # Three octets — not a complete IPv4.
        assert not _has(matcher, "1.2.3", "IPV4")


# ---------------------------------------------------------------------------
# AADHAAR_IN — 12-digit Indian ID
# ---------------------------------------------------------------------------


class TestAadhaarIN:
    def test_no_spaces(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "234123412346", "AADHAAR_IN")

    def test_with_spaces(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "2341 2341 2346", "AADHAAR_IN")

    def test_short_rejected(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "12345", "AADHAAR_IN")


# ---------------------------------------------------------------------------
# PAN_IN — Indian PAN (5 letters + 4 digits + 1 letter)
# ---------------------------------------------------------------------------


class TestPanIN:
    def test_format(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "ABCDE1234F", "PAN_IN")

    def test_lower_rejected(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "abcde1234f", "PAN_IN")

    def test_too_short(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "ABCDE123", "PAN_IN")


# ---------------------------------------------------------------------------
# AWS_ACCESS_KEY_ID
# ---------------------------------------------------------------------------


class TestAwsKeyId:
    def test_akia_prefix(self, matcher: PIIMatcher) -> None:
        # AKIA + 16 uppercase alnum
        assert _has(matcher, "AKIAIOSFODNN7EXAMPLE", "AWS_ACCESS_KEY_ID")

    def test_asia_prefix(self, matcher: PIIMatcher) -> None:
        assert _has(matcher, "ASIAIOSFODNN7EXAMPLE", "AWS_ACCESS_KEY_ID")

    def test_other_prefix_rejected(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "XYZAIOSFODNN7EXAMPLE", "AWS_ACCESS_KEY_ID")


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class TestJwt:
    def test_three_segments(self, matcher: PIIMatcher) -> None:
        # Minimal eyJ-prefixed token; segments only need base64-url chars.
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        assert _has(matcher, token, "JWT")

    def test_missing_signature_rejected(self, matcher: PIIMatcher) -> None:
        # Two segments only.
        assert not _has(matcher, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0", "JWT")

    def test_wrong_prefix_rejected(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "abc.def.ghi", "JWT")


# ---------------------------------------------------------------------------
# GH_PAT — GitHub personal access token
# ---------------------------------------------------------------------------


class TestGhPat:
    def test_format(self, matcher: PIIMatcher) -> None:
        token = "ghp_" + "A" * 36
        assert _has(matcher, token, "GH_PAT")

    def test_short_rejected(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "ghp_short", "GH_PAT")

    def test_wrong_prefix_rejected(self, matcher: PIIMatcher) -> None:
        token = "xxx_" + "A" * 36
        assert not _has(matcher, token, "GH_PAT")


# ---------------------------------------------------------------------------
# PRIVATE_KEY_PEM
# ---------------------------------------------------------------------------


class TestPrivateKeyPem:
    def test_rsa(self, matcher: PIIMatcher) -> None:
        assert _has(
            matcher,
            "-----BEGIN RSA PRIVATE KEY-----",
            "PRIVATE_KEY_PEM",
        )

    def test_ec(self, matcher: PIIMatcher) -> None:
        assert _has(
            matcher,
            "-----BEGIN EC PRIVATE KEY-----",
            "PRIVATE_KEY_PEM",
        )

    def test_no_match_in_unrelated(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "no key here", "PRIVATE_KEY_PEM")


# ---------------------------------------------------------------------------
# ETH_ADDR
# ---------------------------------------------------------------------------


class TestEthAddr:
    def test_lowercase(self, matcher: PIIMatcher) -> None:
        # 0x + 40 hex chars
        addr = "0x" + "a" * 40
        assert _has(matcher, addr, "ETH_ADDR")

    def test_uppercase(self, matcher: PIIMatcher) -> None:
        addr = "0x" + "A" * 40
        assert _has(matcher, addr, "ETH_ADDR")

    def test_short_rejected(self, matcher: PIIMatcher) -> None:
        assert not _has(matcher, "0x123", "ETH_ADDR")


# ---------------------------------------------------------------------------
# Specificity ordering — IBAN > CC > SSN > PHONE_US (used by overlap resolver)
# ---------------------------------------------------------------------------


def test_specificity_ordering() -> None:
    assert SPECIFICITY["IBAN"] > SPECIFICITY["CC_NUMBER"]
    assert SPECIFICITY["CC_NUMBER"] > SPECIFICITY["PHONE_US"]
    assert SPECIFICITY["SSN_US"] > SPECIFICITY["PHONE_US"]
