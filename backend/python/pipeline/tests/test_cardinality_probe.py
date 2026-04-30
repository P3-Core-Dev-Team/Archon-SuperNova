"""Unit tests for the cardinality_probe phase helpers.

Covers the pure functions ``_reclassify`` and ``_eligible_relationships``;
the orchestrator-driven ``run_phase_cardinality_refine`` is exercised
via integration paths separately (it touches Postgres + the extraction
service so isn't a pure unit-test target).
"""

from __future__ import annotations

import pytest

from discovery import cardinality_probe


# --------------------------------------------------------------------- #
# _reclassify
# --------------------------------------------------------------------- #


def test_reclassify_one_to_one():
    assert cardinality_probe._reclassify(
        child_distinct=10, parent_distinct=10, orphans=0,
    ) == "ONE_TO_ONE"


def test_reclassify_many_to_one():
    """Distinct child < distinct parent, no orphans → MANY_TO_ONE."""
    assert cardinality_probe._reclassify(
        child_distinct=5, parent_distinct=10, orphans=0,
    ) == "MANY_TO_ONE"


def test_reclassify_partial_with_high_containment():
    """Some orphans but containment ≥ threshold → PARTIAL."""
    # 2 orphans / 100 distinct = 0.98 containment, above default 0.95
    assert cardinality_probe._reclassify(
        child_distinct=100, parent_distinct=120, orphans=2,
    ) == "PARTIAL"


def test_reclassify_no_relationship_below_threshold():
    """Containment below threshold → NO_RELATIONSHIP."""
    # 50 orphans / 100 distinct = 0.5 containment
    assert cardinality_probe._reclassify(
        child_distinct=100, parent_distinct=200, orphans=50,
    ) == "NO_RELATIONSHIP"


def test_reclassify_zero_distinct_child():
    """Child with no distinct values → NO_RELATIONSHIP (defensive)."""
    assert cardinality_probe._reclassify(
        child_distinct=0, parent_distinct=10, orphans=0,
    ) == "NO_RELATIONSHIP"


def test_reclassify_custom_threshold():
    """Threshold raised → fewer rows pass as PARTIAL."""
    # 99% containment, threshold 0.999 → fails
    out = cardinality_probe._reclassify(
        child_distinct=100, parent_distinct=120, orphans=1,
        containment_threshold=0.999,
    )
    assert out == "NO_RELATIONSHIP"


# --------------------------------------------------------------------- #
# _eligible_relationships
# --------------------------------------------------------------------- #


def test_eligible_filters_by_confidence_floor():
    rows = [
        {"rel_id": 1, "confidence": 0.95, "cardinality": "MANY_TO_ONE"},
        {"rel_id": 2, "confidence": 0.70, "cardinality": "MANY_TO_ONE"},
        {"rel_id": 3, "confidence": None, "cardinality": "MANY_TO_ONE"},
    ]
    out = cardinality_probe._eligible_relationships(rows, confidence_floor=0.85)
    ids = [r["rel_id"] for r in out]
    assert ids == [1]


def test_eligible_skips_one_to_one():
    """ONE_TO_ONE relationships are deliberately not refined."""
    rows = [
        {"rel_id": 1, "confidence": 0.99, "cardinality": "ONE_TO_ONE"},
        {"rel_id": 2, "confidence": 0.99, "cardinality": "MANY_TO_ONE"},
    ]
    out = cardinality_probe._eligible_relationships(rows, confidence_floor=0.85)
    ids = [r["rel_id"] for r in out]
    assert ids == [2]


def test_eligible_empty_input_returns_empty():
    assert cardinality_probe._eligible_relationships([], confidence_floor=0.85) == []


def test_eligible_inclusive_at_floor():
    """Confidence == floor passes (>=, not strict >)."""
    rows = [
        {"rel_id": 1, "confidence": 0.85, "cardinality": "MANY_TO_ONE"},
    ]
    out = cardinality_probe._eligible_relationships(rows, confidence_floor=0.85)
    assert len(out) == 1
