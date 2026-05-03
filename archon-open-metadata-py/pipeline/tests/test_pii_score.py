"""Unit tests for ``discovery.pii_score``.

Two surfaces:
  * ``column_pii_confidence`` — Bayesian aggregation of three signals.
  * ``resolve_overlaps`` — span-overlap resolution that fixes the
    ``card_number_raw`` PHONE_US/CC_NUMBER false-positive collision.
"""
from __future__ import annotations

import pytest

from discovery.pii_score import (
    Match,
    column_pii_confidence,
    resolve_overlaps,
)


# ---------------------------------------------------------------------------
# Bayesian formula
# ---------------------------------------------------------------------------


class TestColumnPiiConfidence:
    def test_all_zero_yields_zero(self) -> None:
        assert column_pii_confidence(0.0, 0.0, 0.0) == 0.0

    def test_all_one_yields_one(self) -> None:
        # 1 - (1-1)(1-1)(1-1) == 1
        assert column_pii_confidence(1.0, 1.0, 1.0) == pytest.approx(1.0)

    def test_name_only(self) -> None:
        # Exact name prior strength = 0.85, no regex/validator evidence.
        assert column_pii_confidence(0.85, 0.0, 0.0) == pytest.approx(0.85)

    def test_validator_only(self) -> None:
        # 100% of regex hits passed validator, but no name prior or regex
        # rate.  Should saturate at exactly the validator term.
        assert column_pii_confidence(0.0, 0.0, 1.0) == pytest.approx(1.0)

    def test_match_rate_saturates_at_30pct(self) -> None:
        """π_match saturates: 30% raw rate → π_match = 1.0."""
        s = column_pii_confidence(0.0, 0.30, 0.0)
        assert s == pytest.approx(1.0)
        s_high = column_pii_confidence(0.0, 0.95, 0.0)
        assert s_high == pytest.approx(1.0)

    def test_match_rate_below_saturation(self) -> None:
        # 0.15 raw → 0.5 π_match.  No name, no validator.
        s = column_pii_confidence(0.0, 0.15, 0.0)
        assert s == pytest.approx(0.5)

    def test_combined(self) -> None:
        # π_name=0.5 (substring), π_match=0.5 (15% raw rate),
        # π_validate=0.8 → 1 - 0.5*0.5*0.2 = 0.95
        s = column_pii_confidence(0.5, 0.15, 0.8)
        assert s == pytest.approx(0.95)

    def test_clamps_above_one(self) -> None:
        # Inputs outside [0,1] are clamped before the Bayesian combination.
        assert column_pii_confidence(2.0, -1.0, 5.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Overlap resolution
# ---------------------------------------------------------------------------


class TestResolveOverlaps:
    def test_no_overlap_keeps_all(self) -> None:
        m1 = Match("EMAIL", "a@b.com", 0, 7, True)
        m2 = Match("PHONE_US", "212-555-1234", 20, 32, True)
        out = resolve_overlaps([m1, m2])
        assert {m.name for m in out} == {"EMAIL", "PHONE_US"}

    def test_cc_wins_over_phone_on_substring(self) -> None:
        """Direct fix for ``card_number_raw`` — CC > PHONE on overlap."""
        # 16-digit Luhn-valid card: 4111111111111111
        # PHONE_US could also match a 10-digit substring, e.g. 1111111111
        cc = Match("CC_NUMBER", "4111111111111111", 0, 16, True)
        phone = Match("PHONE_US", "1111111111", 6, 16, True)
        out = resolve_overlaps([cc, phone])
        assert len(out) == 1
        assert out[0].name == "CC_NUMBER"

    def test_validator_dominance_drops_failed(self) -> None:
        # Two overlapping matches: failed CC + passing PHONE.  The PHONE
        # match wins because the more-specific pattern's validator failed.
        cc_fail = Match("CC_NUMBER", "4111111111111112", 0, 16, False)
        phone = Match("PHONE_US", "111-111-1112", 4, 16, True)
        out = resolve_overlaps([cc_fail, phone])
        # Validator-passing PHONE wins over validator-failed CC.
        assert len(out) == 1
        assert out[0].name == "PHONE_US"
        assert out[0].validated is True

    def test_unvalidated_pair_higher_specificity_wins(self) -> None:
        # Both unvalidated: more specific wins.
        a = Match("PASSPORT_US", "A12345678", 0, 9, False)
        b = Match("API_KEY", "A12345678extra32chars000", 0, 24, False)
        out = resolve_overlaps([a, b])
        assert len(out) == 1
        # PASSPORT_US specificity (8) > API_KEY (3)
        assert out[0].name == "PASSPORT_US"

    def test_disjoint_matches_preserved(self) -> None:
        # Two matches that don't overlap at all — both kept.
        a = Match("EMAIL", "x@y.com", 0, 7, True)
        b = Match("EMAIL", "p@q.org", 30, 37, True)
        out = resolve_overlaps([a, b])
        assert len(out) == 2

    def test_min_overlap_below_threshold_keeps_both(self) -> None:
        # If overlap fraction < 0.5 of the shorter span, keep both.
        a = Match("EMAIL", "user@host.io", 0, 12, True)
        b = Match("EMAIL", "ho", 8, 10, True)
        # Overlap is the full shorter span (2/2 = 1.0), so still merged…
        # use a partial-overlap pair instead.
        c = Match("PHONE_US", "212-555-1234", 0, 12, True)
        d = Match("EMAIL", "a@b.com", 11, 18, True)
        out = resolve_overlaps([c, d])
        assert len(out) == 2  # 1-char overlap is < 50% of either span


# ---------------------------------------------------------------------------
# Property-style sanity
# ---------------------------------------------------------------------------


def test_resolve_overlaps_idempotent() -> None:
    # Resolving the output of resolve_overlaps shouldn't shrink it further.
    matches = [
        Match("EMAIL", "a@b.com", 0, 7, True),
        Match("PHONE_E164", "+15551234567", 20, 32, True),
        Match("DOB", "1990-01-15", 50, 60, True),
    ]
    once = resolve_overlaps(matches)
    twice = resolve_overlaps(once)
    assert {m.name for m in once} == {m.name for m in twice}
