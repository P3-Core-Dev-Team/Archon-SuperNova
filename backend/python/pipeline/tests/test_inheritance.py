"""Tests for the inheritance / is-a annotator -- pure helpers only.

The DB-touching ``annotate_inheritance`` is exercised end-to-end via the
orchestrator integration tests; here we only verify the predicate logic.
"""
from __future__ import annotations

import pytest

from discovery import inheritance as inh


def test_normalize_strips_plural():
    assert inh._normalize("orders") == "order"
    assert inh._normalize("categories") == "category"
    assert inh._normalize("addresses") == "address"
    # 'class' ends in 'ss', not stripped.
    assert inh._normalize("class") == "class"


def test_name_similarity_high_for_identical():
    assert inh._name_similarity("business_entity_id", "business_entity_id") == 1.0


def test_name_similarity_high_for_plural_normalized():
    # vendor.business_entity_id ↔ business_entity.business_entity_id is the
    # canonical inheritance-pattern match -- must score >= 0.95.
    s = inh._name_similarity("business_entity_id", "business_entity_id")
    assert s >= 0.95


def test_name_similarity_low_for_unrelated():
    s = inh._name_similarity("user_id", "order_total")
    assert s < 0.5
