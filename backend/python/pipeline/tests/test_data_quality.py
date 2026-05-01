"""Tests for the data_quality phase's pure helpers.

The DuckDB SQL paths are exercised by the orchestrator-level smoke run
on the adv schema; here we cover ``classify_metrics`` exhaustively
because that's the function deciding which findings get persisted.
"""

from __future__ import annotations

import pytest

from discovery.data_quality import (
    ColumnMetrics,
    IssueType,
    classify_metrics,
)


def _m(**kw):
    """Build a ColumnMetrics with sensible defaults; override with kw.

    ``is_sole_pk`` defaults to True when ``is_pk=True`` (single-column
    PK is the common case) and False otherwise — tests for composite
    PK behaviour pass ``is_sole_pk=False`` explicitly.
    """
    is_pk = kw.get("is_pk", False)
    return ColumnMetrics(
        column_id=kw.get("column_id", 1),
        column_name=kw.get("column_name", "col"),
        sample_rows=kw.get("sample_rows", 1000),
        null_count=kw.get("null_count", 0),
        distinct_count=kw.get("distinct_count", 1000),
        is_pk=is_pk,
        is_sole_pk=kw.get("is_sole_pk", is_pk),
        whitespace_count=kw.get("whitespace_count"),
        empty_count=kw.get("empty_count"),
        distinct_lower_count=kw.get("distinct_lower_count"),
        samples_whitespace=kw.get("samples_whitespace"),
        samples_mixed_case=kw.get("samples_mixed_case"),
    )


# --------------------------------------------------------------------- #
# null density
# --------------------------------------------------------------------- #


def test_null_heavy_above_threshold():
    f = classify_metrics(_m(sample_rows=1000, null_count=600))
    assert any(x["issue_type"] == IssueType.NULL_HEAVY for x in f)
    nh = next(x for x in f if x["issue_type"] == IssueType.NULL_HEAVY)
    assert nh["severity"] == "MEDIUM"
    assert nh["fraction"] == 0.6


def test_null_heavy_below_threshold():
    """Default threshold 0.50; 49% nulls should NOT trip the finding."""
    f = classify_metrics(_m(sample_rows=1000, null_count=490))
    assert not any(x["issue_type"] == IssueType.NULL_HEAVY for x in f)


def test_all_null_outranks_null_heavy():
    """100% null should report ALL_NULL only, not also NULL_HEAVY."""
    f = classify_metrics(_m(sample_rows=1000, null_count=1000, distinct_count=0))
    issues = {x["issue_type"] for x in f}
    assert IssueType.ALL_NULL in issues
    assert IssueType.NULL_HEAVY not in issues
    an = next(x for x in f if x["issue_type"] == IssueType.ALL_NULL)
    assert an["severity"] == "HIGH"
    assert an["fraction"] == 1.0


def test_threshold_override_more_strict():
    """Operator can raise the null-heavy bar via classify_metrics's
    null_threshold kwarg; 60% null shouldn't trip a 0.80 threshold."""
    f = classify_metrics(
        _m(sample_rows=1000, null_count=600), null_threshold=0.80,
    )
    assert not any(x["issue_type"] == IssueType.NULL_HEAVY for x in f)


# --------------------------------------------------------------------- #
# duplicate PK
# --------------------------------------------------------------------- #


def test_duplicate_pk_flagged():
    f = classify_metrics(_m(
        sample_rows=1000, null_count=0, distinct_count=995, is_pk=True,
    ))
    dup = [x for x in f if x["issue_type"] == IssueType.DUPLICATE_PK]
    assert len(dup) == 1
    assert dup[0]["severity"] == "HIGH"
    assert dup[0]["count"] == 5
    assert dup[0]["fraction"] == 0.005


def test_duplicate_pk_ignored_for_non_pk():
    """A non-PK column with duplicates is the normal case — no finding."""
    f = classify_metrics(_m(
        sample_rows=1000, null_count=0, distinct_count=10, is_pk=False,
    ))
    assert not any(x["issue_type"] == IssueType.DUPLICATE_PK for x in f)


def test_unique_pk_no_finding():
    """PK with every row distinct is healthy — no DUPLICATE_PK."""
    f = classify_metrics(_m(
        sample_rows=1000, null_count=0, distinct_count=1000, is_pk=True,
    ))
    assert not any(x["issue_type"] == IssueType.DUPLICATE_PK for x in f)


def test_composite_pk_member_is_not_flagged_as_duplicate_pk():
    """Junction-table columns that are PART of a composite PK have
    duplicates per-column (uniqueness lives on the tuple).  The gate
    on ``is_sole_pk`` keeps every junction table from spawning 2+
    bogus DUPLICATE_PK findings."""
    f = classify_metrics(_m(
        sample_rows=1000, null_count=0, distinct_count=200,
        is_pk=True, is_sole_pk=False,
    ))
    assert not any(x["issue_type"] == IssueType.DUPLICATE_PK for x in f)


# --------------------------------------------------------------------- #
# whitespace + empty string
# --------------------------------------------------------------------- #


def test_whitespace_finding_with_samples():
    f = classify_metrics(_m(
        sample_rows=1000, null_count=0, distinct_count=900,
        whitespace_count=5,
        samples_whitespace=["[ws]' Smith'", "[ws]'Jones '", "[ws]' Doe '"],
    ))
    ws = next(x for x in f if x["issue_type"] == IssueType.LEADING_TRAILING_WHITESPACE)
    assert ws["severity"] == "LOW"
    assert ws["count"] == 5
    assert len(ws["samples"]) == 3


def test_zero_whitespace_no_finding():
    f = classify_metrics(_m(whitespace_count=0))
    assert not any(x["issue_type"] == IssueType.LEADING_TRAILING_WHITESPACE for x in f)


def test_empty_string_finding():
    f = classify_metrics(_m(
        sample_rows=1000, null_count=0,
        empty_count=12,
    ))
    es = next(x for x in f if x["issue_type"] == IssueType.EMPTY_STRING)
    assert es["count"] == 12
    assert es["severity"] == "LOW"


# --------------------------------------------------------------------- #
# mixed case
# --------------------------------------------------------------------- #


def test_mixed_case_when_lower_collapses():
    """COUNT(DISTINCT lower(v)) < COUNT(DISTINCT v) — same logical
    value in different cases."""
    f = classify_metrics(_m(
        sample_rows=1000,
        distinct_count=10,           # 'USA', 'usa', 'CAN', ...
        distinct_lower_count=5,      # 'usa', 'can', ...
        samples_mixed_case=["USA", "usa", "Usa"],
    ))
    mc = next(x for x in f if x["issue_type"] == IssueType.MIXED_CASE)
    assert mc["severity"] == "MEDIUM"
    assert mc["count"] == 5  # collisions = 10 - 5


def test_no_mixed_case_when_aligned():
    """No collisions → no finding."""
    f = classify_metrics(_m(
        distinct_count=10, distinct_lower_count=10,
    ))
    assert not any(x["issue_type"] == IssueType.MIXED_CASE for x in f)


# --------------------------------------------------------------------- #
# low cardinality
# --------------------------------------------------------------------- #


def test_low_cardinality_with_enough_rows():
    f = classify_metrics(_m(
        sample_rows=1500, distinct_count=3, is_pk=False,
    ))
    lc = next(x for x in f if x["issue_type"] == IssueType.LOW_CARDINALITY)
    assert lc["severity"] == "LOW"
    assert lc["count"] == 3


def test_low_cardinality_skipped_on_small_samples():
    """distinct=2 on 50 rows is just a small sample; not informative."""
    f = classify_metrics(_m(
        sample_rows=50, distinct_count=2, is_pk=False,
    ))
    assert not any(x["issue_type"] == IssueType.LOW_CARDINALITY for x in f)


def test_low_cardinality_skipped_on_pk():
    """PK columns are expected to be high-cardinality; suppress."""
    f = classify_metrics(_m(
        sample_rows=1500, distinct_count=3, is_pk=True,
    ))
    assert not any(x["issue_type"] == IssueType.LOW_CARDINALITY for x in f)


# --------------------------------------------------------------------- #
# clean column
# --------------------------------------------------------------------- #


def test_clean_column_produces_no_findings():
    """Healthy column: no nulls, distinct rich, non-text; nothing fires."""
    f = classify_metrics(_m(
        sample_rows=1000, null_count=0, distinct_count=1000,
        is_pk=False,
    ))
    assert f == []


def test_clean_pk_no_findings():
    f = classify_metrics(_m(
        sample_rows=1000, null_count=0, distinct_count=1000, is_pk=True,
    ))
    assert f == []
