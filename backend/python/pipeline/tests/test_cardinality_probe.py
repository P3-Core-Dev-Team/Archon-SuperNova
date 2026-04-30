"""Unit tests for the cardinality_probe phase helpers.

Covers the pure ``_eligible_relationships`` filter; the orchestrator-
driven ``run_phase_cardinality_refine`` is exercised via integration
paths separately (it touches Postgres + the extraction service so
isn't a pure unit-test target).

The earlier ``_reclassify`` helper was deleted: the probe response
shape (total_rows + distinct_count, no orphan count) doesn't carry
enough evidence to safely re-derive PARTIAL or NO_RELATIONSHIP, and
the production path now flips only confirmed MANY_TO_ONE rows whose
child column is unique → ONE_TO_ONE.  Tests for the dead helper
would have falsely advertised coverage of a non-existent code path.
"""

from __future__ import annotations

from discovery import cardinality_probe


def test_eligible_filters_by_confidence_floor():
    rows = [
        {"rel_id": 1, "confidence": 0.95, "cardinality": "MANY_TO_ONE"},
        {"rel_id": 2, "confidence": 0.70, "cardinality": "MANY_TO_ONE"},
        {"rel_id": 3, "confidence": None, "cardinality": "MANY_TO_ONE"},
    ]
    out = cardinality_probe._eligible_relationships(rows, confidence_floor=0.85)
    assert [r["rel_id"] for r in out] == [1]


def test_eligible_only_returns_many_to_one():
    """ONE_TO_ONE / PARTIAL / NO_RELATIONSHIP are deliberately excluded.

    See module docstring: the probe response can authoritatively flip
    only MANY_TO_ONE rows (orphans already proven 0 by Phase 5).
    """
    rows = [
        {"rel_id": 1, "confidence": 0.99, "cardinality": "ONE_TO_ONE"},
        {"rel_id": 2, "confidence": 0.99, "cardinality": "MANY_TO_ONE"},
        {"rel_id": 3, "confidence": 0.99, "cardinality": "PARTIAL"},
        {"rel_id": 4, "confidence": 0.99, "cardinality": "NO_RELATIONSHIP"},
        {"rel_id": 5, "confidence": 0.99, "cardinality": "MANY_TO_MANY"},
    ]
    out = cardinality_probe._eligible_relationships(rows, confidence_floor=0.85)
    assert [r["rel_id"] for r in out] == [2]


def test_eligible_empty_input_returns_empty():
    assert cardinality_probe._eligible_relationships([], confidence_floor=0.85) == []


def test_eligible_inclusive_at_floor():
    """Confidence == floor passes (>=, not strict >)."""
    rows = [
        {"rel_id": 1, "confidence": 0.85, "cardinality": "MANY_TO_ONE"},
    ]
    out = cardinality_probe._eligible_relationships(rows, confidence_floor=0.85)
    assert len(out) == 1


def test_eligible_handles_string_confidence():
    """Confidence may arrive as numeric string from JSONB; float() coerces."""
    rows = [
        {"rel_id": 1, "confidence": "0.92", "cardinality": "MANY_TO_ONE"},
        {"rel_id": 2, "confidence": "0.50", "cardinality": "MANY_TO_ONE"},
    ]
    out = cardinality_probe._eligible_relationships(rows, confidence_floor=0.85)
    assert [r["rel_id"] for r in out] == [1]


def test_eligible_skips_missing_cardinality():
    """A row without a cardinality field is silently skipped."""
    rows = [
        {"rel_id": 1, "confidence": 0.99},  # no cardinality
        {"rel_id": 2, "confidence": 0.99, "cardinality": "MANY_TO_ONE"},
    ]
    out = cardinality_probe._eligible_relationships(rows, confidence_floor=0.85)
    assert [r["rel_id"] for r in out] == [2]
