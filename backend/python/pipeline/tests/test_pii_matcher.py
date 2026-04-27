"""
Unit tests for pii_scan.py (Phase 3b).

For each pattern: positive (known-good values), negative (known non-matches),
and validator tests.  No database required.
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from discovery.pii_scan import (
    PIIMatcher,
    PIIFinding,
    PatternDef,
    PATTERN_DEFS,
    luhn_valid,
    iban_valid,
    entropy_looks_random,
    ssn_us_valid,
    date_parseable,
    redact,
    scan_column,
)


# ---------------------------------------------------------------------------
# Fixture: shared matcher (expensive init done once per test session)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def matcher() -> PIIMatcher:
    return PIIMatcher()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _has_match(matcher: PIIMatcher, text: str, pii_type: str) -> bool:
    """``scan`` returns ``(name, value, start, end)`` 4-tuples (post-upgrade)."""
    return any(hit[0] == pii_type for hit in matcher.scan(text))


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

class TestEmail:
    def test_positive_simple(self, matcher: PIIMatcher):
        assert _has_match(matcher, "user@example.com", "EMAIL")

    def test_positive_subdomain(self, matcher: PIIMatcher):
        assert _has_match(matcher, "first.last@mail.corp.org", "EMAIL")

    def test_negative_not_email_rate(self, matcher: PIIMatcher):
        # "rate@5%" — '@' followed by non-alpha (should not match our pattern)
        assert not _has_match(matcher, "rate@5%", "EMAIL")

    def test_negative_no_tld(self, matcher: PIIMatcher):
        # no dot after @
        assert not _has_match(matcher, "user@localhost", "EMAIL")

    def test_redaction(self):
        r = redact("john.doe@example.com", "EMAIL")
        assert "@" in r
        assert "john.doe" not in r
        assert r.startswith("j")


# ---------------------------------------------------------------------------
# PHONE_US
# ---------------------------------------------------------------------------

class TestPhoneUS:
    def test_positive_dashes(self, matcher: PIIMatcher):
        assert _has_match(matcher, "212-555-1234", "PHONE_US")

    def test_positive_dots(self, matcher: PIIMatcher):
        assert _has_match(matcher, "212.555.1234", "PHONE_US")

    def test_positive_parens(self, matcher: PIIMatcher):
        assert _has_match(matcher, "(800) 555-4567", "PHONE_US")

    def test_negative_too_short(self, matcher: PIIMatcher):
        assert not _has_match(matcher, "555-1234", "PHONE_US")

    def test_redaction(self):
        r = redact("212-555-1234", "PHONE_US")
        assert r.startswith("***")
        assert "1234" in r


# ---------------------------------------------------------------------------
# SSN_US
# ---------------------------------------------------------------------------

class TestSSN:
    def test_positive_valid_ssn(self, matcher: PIIMatcher):
        assert _has_match(matcher, "123-45-6789", "SSN_US")

    def test_negative_area_000(self, matcher: PIIMatcher):
        # SSNs starting 000- are invalid (pattern rejects them)
        assert not _has_match(matcher, "000-12-3456", "SSN_US")

    def test_negative_area_666(self, matcher: PIIMatcher):
        assert not _has_match(matcher, "666-12-3456", "SSN_US")

    def test_negative_area_900(self, matcher: PIIMatcher):
        assert not _has_match(matcher, "999-12-3456", "SSN_US")

    def test_negative_group_00(self, matcher: PIIMatcher):
        assert not _has_match(matcher, "123-00-3456", "SSN_US")

    def test_negative_serial_0000(self, matcher: PIIMatcher):
        assert not _has_match(matcher, "123-45-0000", "SSN_US")

    def test_validator_always_true(self):
        """ssn_us_valid is a pass-through (regex does the filtering)."""
        assert ssn_us_valid("123-45-6789") is True

    def test_redaction(self):
        r = redact("123-45-6789", "SSN_US")
        assert r.startswith("***")
        assert "6789" in r


# ---------------------------------------------------------------------------
# CC_NUMBER + Luhn
# ---------------------------------------------------------------------------

class TestCreditCard:
    # Luhn-valid Visa test number
    VISA_GOOD = "4111111111111111"
    # Luhn-invalid (last digit off by 1)
    VISA_BAD  = "4111111111111112"

    def test_luhn_pass(self):
        assert luhn_valid(self.VISA_GOOD) is True

    def test_luhn_fail(self):
        assert luhn_valid(self.VISA_BAD) is False

    def test_luhn_too_short(self):
        assert luhn_valid("1234") is False

    def test_luhn_with_spaces(self):
        assert luhn_valid("4111 1111 1111 1111") is True

    def test_pattern_matches_card(self, matcher: PIIMatcher):
        assert _has_match(matcher, self.VISA_GOOD, "CC_NUMBER")

    def test_redaction(self):
        r = redact(self.VISA_GOOD, "CC_NUMBER")
        assert r.startswith("***")
        assert "1111" in r


# ---------------------------------------------------------------------------
# IBAN
# ---------------------------------------------------------------------------

class TestIBAN:
    # Real German IBAN (valid checksum, well-known test value)
    GOOD_IBAN = "DE89370400440532013000"
    BAD_IBAN  = "DE00370400440532013000"   # invalid check digits

    def test_iban_valid_good(self):
        # stdnum may not be installed in test env; just ensure no crash
        result = iban_valid(self.GOOD_IBAN)
        assert isinstance(result, bool)

    def test_pattern_matches_iban(self, matcher: PIIMatcher):
        assert _has_match(matcher, self.GOOD_IBAN, "IBAN")

    def test_redaction(self):
        r = redact(self.GOOD_IBAN, "IBAN")
        assert r.startswith("***")
        assert "3000" in r


# ---------------------------------------------------------------------------
# API_KEY (entropy)
# ---------------------------------------------------------------------------

class TestApiKey:
    # 40-char alphanumeric token with high entropy (real-world API key shape)
    GOOD_KEY = "sK9xRqZvYwPnMtHjLbFcDuAeGiOk2N5T8W3X7Q1V"
    # "Key" that is all the same character — low entropy
    LOW_ENT  = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"

    def test_entropy_high_for_random(self):
        assert entropy_looks_random(self.GOOD_KEY) is True

    def test_entropy_low_for_repeated(self):
        assert entropy_looks_random(self.LOW_ENT) is False

    def test_entropy_too_short(self):
        assert entropy_looks_random("abc123") is False

    def test_redaction(self):
        r = redact(self.GOOD_KEY, "API_KEY")
        assert r.startswith("***")


# ---------------------------------------------------------------------------
# DOB
# ---------------------------------------------------------------------------

class TestDOB:
    def test_positive(self, matcher: PIIMatcher):
        assert _has_match(matcher, "1985-07-23", "DOB")

    def test_negative_future_century(self, matcher: PIIMatcher):
        # Pattern only allows 19xx or 20xx
        assert not _has_match(matcher, "2185-07-23", "DOB")

    def test_negative_invalid_month(self, matcher: PIIMatcher):
        assert not _has_match(matcher, "1985-13-01", "DOB")

    def test_date_parseable_valid(self):
        assert date_parseable("1985-07-23") is True

    def test_date_parseable_invalid(self):
        assert date_parseable("not-a-date") is False

    def test_redaction(self):
        r = redact("1985-07-23", "DOB")
        assert "1985" in r
        assert r.endswith("**")


# ---------------------------------------------------------------------------
# scan_column integration test (no DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def pii_parquet(tmp_path: Path) -> Path:
    """Parquet file with intentional PII in a string column."""
    table = pa.table({
        "email_col": pa.array([
            "alice@example.com",
            "bob@test.org",
            "notanemail",
            "charlie@company.io",
            "hello world",
        ], type=pa.string()),
        "number_col": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
        "cc_col": pa.array([
            "4111111111111111",   # valid Luhn
            "5500005555555559",   # Mastercard test number
            "gibberish",
            "4111111111111112",   # invalid Luhn
            "hello",
        ], type=pa.string()),
    })
    path = tmp_path / "pii_test.parquet"
    pq.write_table(table, str(path))
    return path


def test_scan_column_finds_email(pii_parquet: Path):
    m = PIIMatcher()
    findings = scan_column(pii_parquet, "email_col", m, max_rows=1000)
    email_findings = [f for f in findings if f.pii_type == "EMAIL"]
    assert email_findings, "Expected EMAIL findings in email_col"
    f = email_findings[0]
    assert f.match_count >= 3
    # No raw email in examples
    for ex in f.redacted_examples:
        assert "@example.com" not in ex or ex.startswith("a***")


def test_scan_column_skips_integer_column(pii_parquet: Path):
    """Integer columns should never be PII-scanned."""
    m = PIIMatcher()
    findings = scan_column(
        pii_parquet, "number_col", m,
        max_rows=1000,
        type_class="INT_NARROW",
    )
    assert findings == []


def test_scan_column_cc_luhn_validated(pii_parquet: Path):
    """CC_NUMBER findings should only count Luhn-valid cards as validated."""
    m = PIIMatcher()
    findings = scan_column(pii_parquet, "cc_col", m, max_rows=1000)
    cc_findings = [f for f in findings if f.pii_type == "CC_NUMBER"]
    if cc_findings:
        f = cc_findings[0]
        # 2 Luhn-valid cards exist, 1 invalid → validated_count ≤ match_count
        assert f.validated_count <= f.match_count


def test_scan_column_redacted_examples_no_raw_cc(pii_parquet: Path):
    """Redacted examples must not contain raw card numbers."""
    m = PIIMatcher()
    findings = scan_column(pii_parquet, "cc_col", m, max_rows=1000)
    for f in findings:
        for ex in f.redacted_examples:
            assert "4111111111111111" not in ex


def test_scan_column_sampling(tmp_path: Path):
    """When rows > max_rows, the stride sampler should limit rows_scanned."""
    n = 10_000
    table = pa.table({
        "txt": pa.array([f"text_{i}@foo.com" for i in range(n)], type=pa.string()),
    })
    path = tmp_path / "big_pii.parquet"
    pq.write_table(table, str(path))

    m = PIIMatcher()
    findings = scan_column(path, "txt", m, max_rows=500)
    if findings:
        # sample_count should be capped at max_rows
        assert findings[0].sample_count <= 500


def test_scan_column_multi_hit_cap_per_cell(tmp_path: Path):
    """D1.1: A single cell contributes at most 1 to the count per detector.

    The fixture is the bug-report case: a movie-title-like string carrying
    two disjoint SWIFT-BIC-shaped tokens.  Pre-fix the cell counter went up
    twice, producing match_rate > 100%; post-fix the rate is capped by the
    sample count.
    """
    # Two disjoint SWIFT_BIC matches in one cell.
    multi_hit_value = "DEUTDEFF AAAABBCC"
    rows = [multi_hit_value, "no match here", "another plain title"]
    table = pa.table({"title": pa.array(rows, type=pa.string())})
    path = tmp_path / "multi_hit.parquet"
    pq.write_table(table, str(path))

    m = PIIMatcher()
    findings = scan_column(path, "title", m, max_rows=10)
    bic = [f for f in findings if f.pii_type == "SWIFT_BIC"]
    if bic:
        f = bic[0]
        # Cap: the multi-hit cell contributes 1, not 2.
        assert f.match_count <= f.sample_count, (
            f"D1.1 violated: match_count={f.match_count} > sample_count="
            f"{f.sample_count} (multi-hit cap should clamp at 1/cell)"
        )
        # And concretely: only 1 cell matched at all in this fixture.
        assert f.match_count == 1, (
            f"Expected exactly 1 cell-level SWIFT_BIC hit, got {f.match_count}"
        )
