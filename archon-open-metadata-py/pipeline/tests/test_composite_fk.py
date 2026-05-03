"""
Unit tests for composite_fk.py (Phase 4b).

Covers the three specified scenarios plus a couple of helper-level checks
that pin the conservative behaviour from hint 4 / test 3.

No database required: the tests against ``find_composite_fks`` mock the
SQL surface so the pure-DuckDB validation path is exercised in isolation.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from discovery.composite_fk import (
    CompositeFkCandidate,
    _avg_name_similarity,
    _classify,
    _enumerate_subsets,
    _pair_name_similarity_floor,
    _should_propose_composite,
    _validate_composite_one,
    find_composite_fks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def con():
    """In-memory DuckDB connection for each test."""
    c = duckdb.connect()
    c.execute("SET memory_limit = '512MB'")
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Subset enumeration (test 1)
# ---------------------------------------------------------------------------


class TestEnumerateSubsets:
    """test_composite_subsets_size_2:
    Given 3 single FKs between the same two tables, generate C(3,2)=3
    size-2 subsets.
    """

    def _singles(self):
        return [
            {"child_col_id": 1, "parent_col_id": 11, "child_column_name": "a"},
            {"child_col_id": 2, "parent_col_id": 12, "child_column_name": "b"},
            {"child_col_id": 3, "parent_col_id": 13, "child_column_name": "c"},
        ]

    def test_size_two_yields_three_subsets(self):
        subsets = _enumerate_subsets(self._singles(), arity=2)
        assert len(subsets) == 3
        # Each subset is a tuple of two singles.
        for s in subsets:
            assert isinstance(s, tuple)
            assert len(s) == 2

    def test_size_three_yields_one_subset(self):
        subsets = _enumerate_subsets(self._singles(), arity=3)
        assert len(subsets) == 1
        assert len(subsets[0]) == 3

    def test_arity_above_population_returns_empty(self):
        subsets = _enumerate_subsets(self._singles(), arity=4)
        assert subsets == []

    def test_arity_zero_or_negative_returns_empty(self):
        assert _enumerate_subsets(self._singles(), arity=0) == []
        assert _enumerate_subsets(self._singles(), arity=-1) == []

    def test_subsets_preserve_input_order(self):
        singles = self._singles()
        subsets = _enumerate_subsets(singles, arity=2)
        # First subset must be (singles[0], singles[1]) — itertools.combinations
        # contract.  Pin the order so test 1's "size-2 from 3 singles" is
        # deterministic for downstream callers.
        assert subsets[0] == (singles[0], singles[1])
        assert subsets[1] == (singles[0], singles[2])
        assert subsets[2] == (singles[1], singles[2])


# ---------------------------------------------------------------------------
# Skip-when-singles-already-perfect (test 3)
# ---------------------------------------------------------------------------


class TestShouldProposeComposite:
    """test_skip_when_singles_already_perfect:
    When 2 individual FKs are both 100%, the composite is redundant; skip.
    """

    def test_both_singles_perfect_is_skipped(self):
        # Both singles at 1.0 → composite restates the constraint → skip.
        assert _should_propose_composite([1.0, 1.0]) is False

    def test_one_perfect_one_strong_is_proposed(self):
        # Strong but not redundant: 0.97 + 1.0 → composite worth checking.
        assert _should_propose_composite([1.0, 0.97]) is True

    def test_both_strong_is_proposed(self):
        assert _should_propose_composite([0.96, 0.97]) is True

    def test_one_weak_is_skipped(self):
        # Hint 4: every constituent must clear the floor.
        assert _should_propose_composite([0.94, 0.99]) is False

    def test_empty_input_is_skipped(self):
        assert _should_propose_composite([]) is False


# ---------------------------------------------------------------------------
# DuckDB validator (test 2 — single-column non-exact, composite exact)
# ---------------------------------------------------------------------------


class TestValidateCompositeOne:
    """test_composite_validates_via_duckdb:
    Synthetic parquet pair where (a, b) -> (x, y) is exact, but neither
    single column is on its own.  The detector must recognise the composite.
    """

    def _write_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        """Build child/parent parquet files where:

          * parent has rows ``(x, y)`` covering exactly the child pairs;
          * neither x nor y alone is unique to the matching rows — both
            children and parent contain x and y values that overlap with
            other rows (so single-column containment < 1.0 unless the
            join is on the pair).
        """
        # Parent table: composite key (x, y) is the natural PK.
        # Repeat values across rows so single-column projections OVERLAP
        # outside of the matching pairs.
        parent = pa.table(
            {
                # Each x and y value individually appears in MORE rows than
                # the child references — so single-col containment for x or
                # y alone is < 1.0 (orphans appear because some child x's
                # don't match the same parent rows).
                "x": pa.array([1, 1, 2, 2, 3, 3, 4, 4], type=pa.int64()),
                "y": pa.array([10, 20, 10, 20, 10, 20, 10, 20], type=pa.int64()),
                "label": pa.array(
                    ["A", "B", "C", "D", "E", "F", "G", "H"],
                    type=pa.string(),
                ),
            }
        )
        # Child table: every row's (a, b) MUST match a parent (x, y).
        # Single-column 'a' values are subset of parent.x — but each child a
        # exists in parent for both y=10 AND y=20, so `a` alone has parent
        # multiplicity > 1; same for `b`.  The composite (a, b) is unique.
        child = pa.table(
            {
                "a": pa.array([1, 2, 3, 4], type=pa.int64()),
                "b": pa.array([10, 20, 10, 20], type=pa.int64()),
            }
        )
        cp = tmp_path / "child_composite.parquet"
        pp = tmp_path / "parent_composite.parquet"
        pq.write_table(child, str(cp))
        pq.write_table(parent, str(pp))
        return cp, pp

    def test_composite_exact_match(self, tmp_path: Path, con):
        cp, pp = self._write_pair(tmp_path)
        result = _validate_composite_one(
            con,
            child_parquet=cp,
            child_cols=["a", "b"],
            parent_parquet=pp,
            parent_cols=["x", "y"],
            containment_threshold=0.95,
        )
        # Every child (a, b) is in parent (x, y).
        assert result.containment_full == pytest.approx(1.0)
        assert result.orphan_count == 0
        assert result.child_distinct == 4
        # Parent has 8 distinct (x, y) pairs — child references 4 of them.
        assert result.parent_distinct == 8
        # 4 distinct children, 8 distinct parents → MANY_TO_ONE.
        assert result.cardinality == "MANY_TO_ONE"

    def test_composite_partial_when_pair_missing(self, tmp_path: Path, con):
        """If the child has a (a, b) pair that's NOT in parent (x, y),
        the composite has orphans even though both columns individually
        overlap parent's x / y values."""
        # Parent: only (1, 10) and (2, 20)
        parent = pa.table(
            {
                "x": pa.array([1, 2], type=pa.int64()),
                "y": pa.array([10, 20], type=pa.int64()),
            }
        )
        # Child: (1, 10) is in parent; (1, 20) is NOT (orphan composite even
        # though x=1 and y=20 each individually appear in parent).
        child = pa.table(
            {
                "a": pa.array([1, 1], type=pa.int64()),
                "b": pa.array([10, 20], type=pa.int64()),
            }
        )
        cp = tmp_path / "c_partial.parquet"
        pp = tmp_path / "p_partial.parquet"
        pq.write_table(child, str(cp))
        pq.write_table(parent, str(pp))

        result = _validate_composite_one(
            con,
            child_parquet=cp,
            child_cols=["a", "b"],
            parent_parquet=pp,
            parent_cols=["x", "y"],
            containment_threshold=0.95,
        )
        assert result.orphan_count == 1
        # 2 distinct child pairs, 1 orphan → containment = 0.5
        assert result.containment_full == pytest.approx(0.5)
        # Below 0.95 threshold → NO_RELATIONSHIP rather than PARTIAL.
        assert result.cardinality == "NO_RELATIONSHIP"

    def test_classify_helper(self):
        # Sanity check on the shared classifier.
        c, label = _classify(cd=4, pd=8, orphans=0, containment_threshold=0.95)
        assert c == 1.0
        assert label == "MANY_TO_ONE"

        c, label = _classify(cd=10, pd=10, orphans=0, containment_threshold=0.95)
        assert c == 1.0
        assert label == "ONE_TO_ONE"

        c, label = _classify(cd=10, pd=10, orphans=1, containment_threshold=0.8)
        assert c == 0.9
        assert label == "PARTIAL"

        c, label = _classify(cd=10, pd=10, orphans=5, containment_threshold=0.8)
        assert c == 0.5
        assert label == "NO_RELATIONSHIP"


# ---------------------------------------------------------------------------
# Name-similarity helpers (used by the pre-filter & dataclass output)
# ---------------------------------------------------------------------------


class TestNameSimilarityHelpers:
    def test_pair_floor_uses_min(self):
        # First pair perfect, second pair very low — floor must be the low one.
        floor = _pair_name_similarity_floor(["x", "y"], ["x", "totally_different"])
        assert floor < 0.5

    def test_avg_uses_mean(self):
        avg = _avg_name_similarity(["x", "y"], ["x", "y"])
        assert avg == pytest.approx(1.0)

    def test_mismatched_lengths_return_zero(self):
        assert _pair_name_similarity_floor(["a"], ["a", "b"]) == 0.0
        assert _avg_name_similarity(["a"], ["a", "b"]) == 0.0

    def test_empty_returns_zero(self):
        assert _pair_name_similarity_floor([], []) == 0.0
        assert _avg_name_similarity([], []) == 0.0


# ---------------------------------------------------------------------------
# End-to-end find_composite_fks (mocked engine, real DuckDB)
# ---------------------------------------------------------------------------


class _FakeMappingResult:
    def __init__(self, rows: list):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeRowResult:
    """Plain ``Result`` (no .mappings()) for the pii_findings select."""

    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _CapturingConn:
    def __init__(self, rows_list, pii_rows):
        self._rows = list(rows_list)
        self._pii_rows = list(pii_rows)
        # Track which kind of select we're answering by call count.
        self._call = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt):
        self._call += 1
        # First call: the singles select with multiple columns.
        # Second call: the pii_findings.column_id select.
        if self._call == 1:
            return _FakeMappingResult(self._rows)
        return _FakeRowResult(self._pii_rows)


class _FakeEngine:
    def __init__(self, rows, pii_rows):
        self._rows = rows
        self._pii_rows = pii_rows

    def connect(self):
        return _CapturingConn(self._rows, self._pii_rows)


class TestFindCompositeFksEndToEnd:
    """Drive ``find_composite_fks`` with a fake engine and a real parquet pair.

    The fake engine returns two single-column FK candidate rows that share
    a (child_table, parent_table); the DuckDB validator then runs against
    actual parquet files to confirm the composite.
    """

    def _write_files(self, tmp_path: Path) -> tuple[Path, Path]:
        # Composite (order_id, line_no) is exact; neither column is on its own.
        # Use realistic column names so the name-similarity pre-filter is
        # satisfied — the spec hint 5 says composite columns should have
        # correlated names on each side.
        parent = pa.table(
            {
                "order_id": pa.array([1, 1, 2, 2, 3, 3, 4, 4], type=pa.int64()),
                "line_no":  pa.array(
                    [10, 20, 10, 20, 10, 20, 10, 20], type=pa.int64()
                ),
            }
        )
        child = pa.table(
            {
                "order_id": pa.array([1, 2, 3, 4], type=pa.int64()),
                "line_no":  pa.array([10, 20, 10, 20], type=pa.int64()),
            }
        )
        cp = tmp_path / "child_e2e.parquet"
        pp = tmp_path / "parent_e2e.parquet"
        pq.write_table(child, str(cp))
        pq.write_table(parent, str(pp))
        return cp, pp

    def _build_rows(self, cp: Path, pp: Path) -> list:
        # Both single-column candidates have estimated_containment 0.97 each
        # (high enough to cross the singles floor, low enough that neither
        # is "already perfect" — so the redundancy gate doesn't fire).
        return [
            {
                "candidate_id": 1,
                "child_col_id": 101,
                "parent_col_id": 201,
                "estimated_containment": 0.97,
                "name_similarity": 1.0,
                "child_column_name": "order_id",
                "parent_column_name": "order_id",
                "child_table_id": 10,
                "child_table_name": "orders",
                "child_parquet_path": str(cp),
                "parent_table_id": 20,
                "parent_table_name": "order_items",
                "parent_parquet_path": str(pp),
            },
            {
                "candidate_id": 2,
                "child_col_id": 102,
                "parent_col_id": 202,
                "estimated_containment": 0.97,
                "name_similarity": 1.0,
                "child_column_name": "line_no",
                "parent_column_name": "line_no",
                "child_table_id": 10,
                "child_table_name": "orders",
                "child_parquet_path": str(cp),
                "parent_table_id": 20,
                "parent_table_name": "order_items",
                "parent_parquet_path": str(pp),
            },
        ]

    def test_detects_composite(self, tmp_path: Path):
        cp, pp = self._write_files(tmp_path)
        rows = self._build_rows(cp, pp)
        engine = _FakeEngine(rows, pii_rows=[])
        cfg = SimpleNamespace(
            relationships=SimpleNamespace(containment_threshold=0.95)
        )

        results = find_composite_fks(engine, cfg)

        assert len(results) == 1
        out = results[0]
        assert isinstance(out, CompositeFkCandidate)
        assert out.child_table == "orders"
        assert out.parent_table == "order_items"
        # Order is set by the pre-sort on child column name (alphabetical):
        # 'line_no' before 'order_id'.  The validator preserves that order.
        assert sorted(out.child_columns) == ["line_no", "order_id"]
        assert sorted(out.parent_columns) == ["line_no", "order_id"]
        # Same names on both sides → child[i] aligns with parent[i].
        assert out.child_columns == out.parent_columns
        assert out.containment == pytest.approx(1.0)
        assert out.cardinality in ("MANY_TO_ONE", "ONE_TO_ONE")
        # Identical names on both sides → average similarity is 1.0.
        assert out.name_similarity == pytest.approx(1.0)
        assert out.child_table_id == 10
        assert out.parent_table_id == 20
        assert sorted(out.child_col_ids) == [101, 102]
        assert sorted(out.parent_col_ids) == [201, 202]

    def test_pii_columns_skipped(self, tmp_path: Path):
        cp, pp = self._write_files(tmp_path)
        rows = self._build_rows(cp, pp)
        # Mark the child column 102 as PII — composite must be skipped.
        engine = _FakeEngine(rows, pii_rows=[(102,)])
        cfg = SimpleNamespace(
            relationships=SimpleNamespace(containment_threshold=0.95)
        )

        results = find_composite_fks(engine, cfg)
        assert results == []

    def test_singles_already_perfect_skipped(self, tmp_path: Path):
        """When BOTH singles are already 100% containment, the composite is
        redundant and must NOT be emitted (test 3, end-to-end edition)."""
        cp, pp = self._write_files(tmp_path)
        rows = self._build_rows(cp, pp)
        # Bump both singles to 1.0 — redundancy gate fires.
        for r in rows:
            r["estimated_containment"] = 1.0
        engine = _FakeEngine(rows, pii_rows=[])
        cfg = SimpleNamespace(
            relationships=SimpleNamespace(containment_threshold=0.95)
        )
        results = find_composite_fks(engine, cfg)
        assert results == []
