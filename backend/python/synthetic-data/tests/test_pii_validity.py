"""Test that generated PII values pass real validation checks."""

from __future__ import annotations

import re
import os
from pathlib import Path

import pyarrow.parquet as pq
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYNTHETIC_DIR = Path(os.environ.get("SYNTHETIC_DIR", "./synthetic"))
SCHEMAS_DIR = SYNTHETIC_DIR / "schemas"


def _load_column(table_name: str, column_name: str, n: int = 2000) -> list:
    """Load up to n non-null values from a column in a parquet file."""
    path = SCHEMAS_DIR / f"{table_name}.parquet"
    if not path.exists():
        pytest.skip(f"Parquet file not found: {path}. Run generation first.")
    tbl = pq.read_table(path, columns=[column_name])
    col = tbl.column(column_name)
    vals = [v.as_py() for v in col if v.is_valid and v.as_py() is not None]
    return vals[:n]


RFC5322_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def _luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmailValidity:
    def test_customers_email(self):
        emails = _load_column("customers", "email", n=1000)
        assert len(emails) >= 1000, f"Expected 1000+ emails, got {len(emails)}"
        invalid = [e for e in emails if not RFC5322_RE.match(e)]
        assert len(invalid) == 0, f"Invalid emails: {invalid[:5]}"

    def test_users_email(self):
        emails = _load_column("users", "email", n=1000)
        assert len(emails) >= 500
        invalid = [e for e in emails if not RFC5322_RE.match(e)]
        assert len(invalid) == 0, f"Invalid user emails: {invalid[:5]}"

    def test_employee_work_email(self):
        emails = _load_column("employee_records", "work_email", n=500)
        assert len(emails) >= 100
        invalid = [e for e in emails if not RFC5322_RE.match(e)]
        assert len(invalid) == 0, f"Invalid work emails: {invalid[:5]}"


class TestCreditCardValidity:
    def test_payments_card_number_raw_luhn(self):
        cards = _load_column("payments", "card_number_raw", n=1000)
        assert len(cards) >= 1000, f"Expected 1000+ CC numbers, got {len(cards)}"
        invalid = [c for c in cards if not _luhn_valid(c)]
        assert len(invalid) == 0, f"Luhn-invalid cards: {invalid[:5]}"

    def test_warehouse_tracking_fails_luhn(self):
        """Tracking numbers should look like CC but FAIL Luhn."""
        tracking = _load_column("warehouse_stock", "tracking_number", n=500)
        assert len(tracking) >= 100
        # At least 90% should fail Luhn (the intent is noise values)
        passing = [t for t in tracking if _luhn_valid(t)]
        fail_rate = 1.0 - len(passing) / len(tracking)
        assert fail_rate >= 0.9, f"Expected >=90% to fail Luhn, got {fail_rate:.2%} failing"

    def test_card_last4_is_4_digits(self):
        last4s = _load_column("payments", "card_number_last4", n=500)
        assert len(last4s) >= 100
        invalid = [v for v in last4s if not re.match(r"^\d{4}$", v)]
        assert len(invalid) == 0, f"Invalid last4: {invalid[:5]}"


class TestIBANValidity:
    def test_payments_iban(self):
        ibans = _load_column("payments", "iban", n=1000)
        assert len(ibans) >= 1000, f"Expected 1000+ IBANs, got {len(ibans)}"

        # Validate checksums
        def iban_valid(iban: str) -> bool:
            iban = iban.replace(" ", "").upper()
            if len(iban) < 5:
                return False
            rearranged = iban[4:] + iban[:4]
            numeric = ""
            for ch in rearranged:
                if ch.isalpha():
                    numeric += str(ord(ch) - ord("A") + 10)
                else:
                    numeric += ch
            try:
                return int(numeric) % 97 == 1
            except ValueError:
                return False

        invalid = [ib for ib in ibans if not iban_valid(ib)]
        # Allow up to 1% invalid (some BBAN structures may not validate perfectly)
        invalid_rate = len(invalid) / len(ibans)
        assert invalid_rate <= 0.01, f"Too many invalid IBANs ({invalid_rate:.2%}): {invalid[:3]}"


class TestSSNValidity:
    def test_employee_ssn_format(self):
        ssns = _load_column("employee_records", "ssn", n=500)
        assert len(ssns) >= 100
        pattern = re.compile(r"^\d{3}-\d{2}-\d{4}$")
        invalid_format = [s for s in ssns if not pattern.match(s)]
        assert len(invalid_format) == 0, f"Bad SSN format: {invalid_format[:5]}"

        # No area code 000, 666, or 900-999
        def bad_area(ssn):
            area = int(ssn.split("-")[0])
            return area == 0 or area == 666 or area >= 900
        bad = [s for s in ssns if bad_area(s)]
        assert len(bad) == 0, f"SSNs with reserved area: {bad[:5]}"

    def test_employee_id_999_prefix(self):
        """employee_id should use 999 prefix (noise, not real SSN)."""
        emp_ids = _load_column("employee_records", "employee_id", n=200)
        assert len(emp_ids) >= 100
        non_999 = [e for e in emp_ids if not e.startswith("999-")]
        assert len(non_999) == 0, f"employee_ids not using 999 prefix: {non_999[:5]}"


class TestAPITokens:
    def test_api_token_length(self):
        tokens = _load_column("api_tokens", "token", n=200)
        assert len(tokens) >= 100
        short = [t for t in tokens if len(t) < 40]
        assert len(short) == 0, f"API tokens shorter than 40 chars: {short[:5]}"

    def test_secret_hash_hex(self):
        hashes = _load_column("api_tokens", "secret_hash", n=200)
        assert len(hashes) >= 100
        pattern = re.compile(r"^[0-9a-f]{64}$")
        invalid = [h for h in hashes if not pattern.match(h)]
        assert len(invalid) == 0, f"Bad secret hashes: {invalid[:5]}"


class TestPhoneE164:
    """Phone numbers should be predominantly E.164 so strict detectors hit recall."""

    E164_RE = re.compile(r"^\+1\d{10}$")

    def test_customers_phone_mostly_e164(self):
        phones = _load_column("customers", "phone", n=2000)
        assert len(phones) >= 500, f"Expected lots of phones, got {len(phones)}"
        e164_count = sum(1 for p in phones if self.E164_RE.match(p))
        ratio = e164_count / len(phones)
        # Spec: ≥85% strict E.164 (target is 90%; small sample tolerance).
        assert ratio >= 0.85, f"Only {ratio:.2%} of customers.phone are strict E.164"

    def test_employee_phone_mostly_e164(self):
        phones = _load_column("employee_records", "phone", n=500)
        assert len(phones) >= 100
        e164_count = sum(1 for p in phones if self.E164_RE.match(p))
        ratio = e164_count / len(phones)
        assert ratio >= 0.85, f"Only {ratio:.2%} of employee_records.phone are strict E.164"
