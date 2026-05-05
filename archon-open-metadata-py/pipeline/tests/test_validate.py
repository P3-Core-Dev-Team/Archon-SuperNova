"""
Unit tests for validate.py (Phase 5).

Creates two Parquet files (parent, child) with known containment
and asserts validate_one returns the expected cardinality and containment.
No database required.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from discovery.validate import (
    ValidationResult,
    exact_containment_topk,
    run_phase_5,
    validate_group,
    validate_one,
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


@pytest.fixture
def parent_parquet(tmp_path: Path) -> Path:
    """Parent table: id 1..100, all distinct."""
    table = pa.table({
        "id":   pa.array(list(range(1, 101)), type=pa.int64()),
        "name": pa.array([f"name_{i}" for i in range(1, 101)], type=pa.string()),
    })
    path = tmp_path / "parent.parquet"
    pq.write_table(table, str(path))
    return path


@pytest.fixture
def child_full_parquet(tmp_path: Path) -> Path:
    """
    Child table with 50 rows, all values a subset of parent.id (1..50).
    Each parent ID referenced exactly once → MANY_TO_ONE.
    """
    table = pa.table({
        "parent_id": pa.array(list(range(1, 51)), type=pa.int64()),
        "value":     pa.array([f"v_{i}" for i in range(50)], type=pa.string()),
    })
    path = tmp_path / "child_full.parquet"
    pq.write_table(table, str(path))
    return path


@pytest.fixture
def child_partial_parquet(tmp_path: Path) -> Path:
    """
    Child table with 10 orphan rows (parent IDs 101..110 don't exist in parent).
    """
    table = pa.table({
        "parent_id": pa.array(list(range(1, 41)) + list(range(101, 111)), type=pa.int64()),
    })
    path = tmp_path / "child_partial.parquet"
    pq.write_table(table, str(path))
    return path


@pytest.fixture
def child_one_to_one_parquet(tmp_path: Path) -> Path:
    """Child table where child distinct == parent distinct and no orphans → ONE_TO_ONE."""
    table = pa.table({
        "parent_id": pa.array(list(range(1, 101)), type=pa.int64()),
    })
    path = tmp_path / "child_oto.parquet"
    pq.write_table(table, str(path))
    return path


# ---------------------------------------------------------------------------
# Containment = 1.0 (full subset): MANY_TO_ONE
# ---------------------------------------------------------------------------

class TestFullContainment:
    def test_containment_is_1(self, con, parent_parquet, child_full_parquet):
        result = validate_one(
            con, child_full_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.containment_full == pytest.approx(1.0)

    def test_cardinality_many_to_one(self, con, parent_parquet, child_full_parquet):
        result = validate_one(
            con, child_full_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.cardinality == "MANY_TO_ONE"

    def test_orphan_count_zero(self, con, parent_parquet, child_full_parquet):
        result = validate_one(
            con, child_full_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.orphan_count == 0

    def test_child_distinct_correct(self, con, parent_parquet, child_full_parquet):
        result = validate_one(
            con, child_full_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.child_distinct == 50

    def test_parent_distinct_correct(self, con, parent_parquet, child_full_parquet):
        result = validate_one(
            con, child_full_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.parent_distinct == 100


# ---------------------------------------------------------------------------
# ONE_TO_ONE
# ---------------------------------------------------------------------------

class TestOneToOne:
    def test_cardinality_one_to_one(self, con, parent_parquet, child_one_to_one_parquet):
        result = validate_one(
            con, child_one_to_one_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.cardinality == "ONE_TO_ONE"

    def test_containment_one(self, con, parent_parquet, child_one_to_one_parquet):
        result = validate_one(
            con, child_one_to_one_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.containment_full == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Partial containment
# ---------------------------------------------------------------------------

class TestPartialContainment:
    def test_containment_below_1(self, con, parent_parquet, child_partial_parquet):
        result = validate_one(
            con, child_partial_parquet, "parent_id", parent_parquet, "id"
        )
        # 40 valid + 10 orphans → containment = 40/50 = 0.8
        assert result.containment_full == pytest.approx(0.8, abs=0.01)

    def test_orphan_count_nonzero(self, con, parent_parquet, child_partial_parquet):
        result = validate_one(
            con, child_partial_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.orphan_count == 10

    def test_cardinality_partial(self, con, parent_parquet, child_partial_parquet):
        # 80% containment < 95% threshold → NO_RELATIONSHIP (not PARTIAL)
        result = validate_one(
            con, child_partial_parquet, "parent_id", parent_parquet, "id",
            containment_threshold=0.95,
        )
        assert result.cardinality == "NO_RELATIONSHIP"

    def test_cardinality_partial_at_low_threshold(self, con, parent_parquet, child_partial_parquet):
        # At a lower threshold (0.75), 80% containment qualifies as PARTIAL
        result = validate_one(
            con, child_partial_parquet, "parent_id", parent_parquet, "id",
            containment_threshold=0.75,
        )
        assert result.cardinality == "PARTIAL"


# ---------------------------------------------------------------------------
# Result fields
# ---------------------------------------------------------------------------

class TestResultFields:
    def test_returns_validation_result(self, con, parent_parquet, child_full_parquet):
        result = validate_one(
            con, child_full_parquet, "parent_id", parent_parquet, "id"
        )
        assert isinstance(result, ValidationResult)

    def test_duration_ms_nonneg(self, con, parent_parquet, child_full_parquet):
        result = validate_one(
            con, child_full_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.query_duration_ms >= 0

    def test_table_names_populated(self, con, parent_parquet, child_full_parquet):
        result = validate_one(
            con, child_full_parquet, "parent_id", parent_parquet, "id"
        )
        assert result.child_table == "child_full"
        assert result.parent_table == "parent"

    def test_containment_in_range(self, con, parent_parquet, child_partial_parquet):
        result = validate_one(
            con, child_partial_parquet, "parent_id", parent_parquet, "id"
        )
        assert 0.0 <= result.containment_full <= 1.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_relationship(self, tmp_path: Path, con):
        """Completely disjoint sets → containment=0, NO_RELATIONSHIP."""
        parent = pa.table({"id": pa.array([1, 2, 3], type=pa.int64())})
        child  = pa.table({"pid": pa.array([100, 200, 300], type=pa.int64())})
        pp = tmp_path / "parent_edge.parquet"
        cp = tmp_path / "child_edge.parquet"
        pq.write_table(parent, str(pp))
        pq.write_table(child, str(cp))

        result = validate_one(con, cp, "pid", pp, "id", containment_threshold=0.95)
        assert result.containment_full == pytest.approx(0.0)
        assert result.cardinality == "NO_RELATIONSHIP"

    def test_string_column(self, tmp_path: Path, con):
        """String FK (e.g. UUID-like codes)."""
        parent = pa.table({"code": pa.array(["A", "B", "C", "D"], type=pa.string())})
        child  = pa.table({"ref":  pa.array(["A", "B"], type=pa.string())})
        pp = tmp_path / "str_parent.parquet"
        cp = tmp_path / "str_child.parquet"
        pq.write_table(parent, str(pp))
        pq.write_table(child, str(cp))

        result = validate_one(con, cp, "ref", pp, "code")
        assert result.containment_full == pytest.approx(1.0)
        assert result.cardinality in ("MANY_TO_ONE", "ONE_TO_ONE")

    def test_type_mismatch_int_vs_string(self, tmp_path: Path, con):
        """Disallow comparing INTEGER vs STRING — TYPE_MISMATCH, no DuckDB join run."""
        parent = pa.table({"id": pa.array([1, 2, 3], type=pa.int64())})
        child  = pa.table({"ref": pa.array(["1", "2"], type=pa.string())})
        pp = tmp_path / "mismatch_parent.parquet"
        cp = tmp_path / "mismatch_child.parquet"
        pq.write_table(parent, str(pp))
        pq.write_table(child, str(cp))

        result = validate_one(con, cp, "ref", pp, "id")
        assert result.cardinality == "TYPE_MISMATCH"
        assert result.containment_full == 0.0


# ---------------------------------------------------------------------------
# B4: validate_group — parent-set materialisation
# ---------------------------------------------------------------------------


class TestValidateGroup:
    """Two children sharing one parent — both should reuse the same parent_set."""

    def test_two_children_share_parent_distinct(
        self, tmp_path: Path, con, parent_parquet,
    ):
        # Two children, both pointing at parent.id
        child1 = pa.table({"pid": pa.array(list(range(1, 51)), type=pa.int64())})
        child2 = pa.table({"pid": pa.array(list(range(1, 21)), type=pa.int64())})
        c1p = tmp_path / "g_child1.parquet"
        c2p = tmp_path / "g_child2.parquet"
        pq.write_table(child1, str(c1p))
        pq.write_table(child2, str(c2p))

        results = validate_group(
            con,
            parent_parquet=parent_parquet,
            parent_col="id",
            children=[
                (c1p, "pid", None, None),
                (c2p, "pid", None, None),
            ],
            containment_threshold=0.95,
        )
        assert len(results) == 2
        # Both share identical parent_distinct (B4 guarantees one materialisation)
        assert results[0].parent_distinct == results[1].parent_distinct == 100
        # Each child's child_distinct is independent
        assert results[0].child_distinct == 50
        assert results[1].child_distinct == 20
        # Both fully contained
        assert results[0].containment_full == pytest.approx(1.0)
        assert results[1].containment_full == pytest.approx(1.0)
        assert results[0].cardinality == "MANY_TO_ONE"
        assert results[1].cardinality == "MANY_TO_ONE"

    def test_temp_table_dropped_after_group(
        self, tmp_path: Path, con, parent_parquet,
    ):
        """parent_set temp table must NOT survive validate_group — running twice
        with different parents should not collide."""
        child = pa.table({"pid": pa.array([1, 2, 3], type=pa.int64())})
        cp = tmp_path / "g_temp_child.parquet"
        pq.write_table(child, str(cp))

        validate_group(
            con,
            parent_parquet=parent_parquet,
            parent_col="id",
            children=[(cp, "pid", None, None)],
        )
        # parent_set should be dropped (DROP TABLE IF EXISTS won't error)
        rows = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = 'parent_set'"
        ).fetchone()
        assert rows[0] == 0

    def test_mixed_type_mismatch_in_group(
        self, tmp_path: Path, con, parent_parquet,
    ):
        """One STRING child against an INTEGER parent → TYPE_MISMATCH; the other
        INTEGER child still validates correctly via the materialised parent_set."""
        ok_child = pa.table({"pid": pa.array([1, 2, 3], type=pa.int64())})
        bad_child = pa.table({"pid": pa.array(["1", "2"], type=pa.string())})
        ok_p = tmp_path / "ok_child.parquet"
        bad_p = tmp_path / "bad_child.parquet"
        pq.write_table(ok_child, str(ok_p))
        pq.write_table(bad_child, str(bad_p))

        results = validate_group(
            con,
            parent_parquet=parent_parquet,
            parent_col="id",
            children=[
                (ok_p, "pid", None, None),
                (bad_p, "pid", None, None),
            ],
        )
        assert len(results) == 2
        # Order is preserved: ok child first, bad child second
        ok, bad = results[0], results[1]
        assert ok.cardinality == "MANY_TO_ONE"
        assert bad.cardinality == "TYPE_MISMATCH"
        # The OK child still gets parent_distinct=100 from the materialised set
        assert ok.parent_distinct == 100

    def test_phys_type_hint_skips_describe(
        self, tmp_path: Path, con, parent_parquet,
    ):
        """When physical_type hints are supplied, validate_group should still
        produce correct results without needing DESCRIBE round-trips."""
        child = pa.table({"pid": pa.array([1, 2, 3], type=pa.int64())})
        cp = tmp_path / "hinted_child.parquet"
        pq.write_table(child, str(cp))

        results = validate_group(
            con,
            parent_parquet=parent_parquet,
            parent_col="id",
            # Hint child=BIGINT, parent=BIGINT — both INTEGER family
            children=[(cp, "pid", "BIGINT", "BIGINT")],
        )
        assert len(results) == 1
        assert results[0].cardinality == "MANY_TO_ONE"
        assert results[0].child_distinct == 3
        assert results[0].parent_distinct == 100


# ---------------------------------------------------------------------------
# F1.1 / config wiring: validate_workers field name
# ---------------------------------------------------------------------------


class TestWorkersConfigFieldName:
    """F1.1: ``getattr(workers_cfg, 'validate', 8)`` returned the inherited
    ``BaseModel.validate`` bound method instead of the int.  After the fix,
    ``run_phase_5`` reads ``validate_workers`` (Python attr; YAML alias
    'validate') and gets a real integer.
    """

    def test_validate_workers_returns_int(self):
        from discovery.config import WorkersConfig

        wc = WorkersConfig()
        v = getattr(wc, "validate_workers", 8)
        assert isinstance(v, int), (
            f"expected int, got {type(v).__name__}={v!r}"
        )
        assert v == 8

    def test_validate_workers_honours_yaml_alias(self):
        """Constructor accepts both Python name and YAML alias."""
        from discovery.config import WorkersConfig

        wc_alias = WorkersConfig(validate=12)
        wc_python = WorkersConfig(validate_workers=12)
        assert wc_alias.validate_workers == 12
        assert wc_python.validate_workers == 12


# ---------------------------------------------------------------------------
# F1.2 / A5: tier='primary' filter in run_phase_5
#
# Strategy: monkeypatch ``engine.connect()`` to return a fake connection that
# captures the compiled SELECT.  The pending list is empty so the function
# short-circuits at "phase5_nothing_to_do" — no Pool, no DAOs needed.
# ---------------------------------------------------------------------------


class _FakeMappingResult:
    """Minimal stand-in for ``Result.mappings().all()`` returning a fixed list."""

    def __init__(self, rows: list):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _CapturingConn:
    """Fake SQLAlchemy connection: captures execute() statements.

    Returns the rows from ``rows_for(stmt)`` — a callable that decides what
    to return based on the statement text.  The ``__enter__`` / ``__exit__``
    pair makes this usable as a ``with engine.connect() as conn:`` target.
    """

    def __init__(self, rows_for, captured_sql: list):
        self._rows_for = rows_for
        self._captured = captured_sql

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt):
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self._captured.append(sql)
        rows = self._rows_for(stmt, sql)
        return _FakeMappingResult(rows)


class _FakeEngine:
    def __init__(self, rows_for, captured_sql):
        self._rows_for = rows_for
        self._captured = captured_sql

    def connect(self):
        return _CapturingConn(self._rows_for, self._captured)

    def begin(self):
        return _CapturingConn(self._rows_for, self._captured)


class TestPhase5TierFilter:
    """F1.2: ``run_phase_5`` must add ``WHERE fk_candidates.tier='primary'``
    when ``config.relationships.validate_only_primary_tier`` is True (default).
    """

    def _empty_rows_for(self, stmt, sql):
        return []

    def test_default_config_emits_tier_primary_filter(self):
        """No explicit relationships block → default behaviour → filter ON."""
        captured: list[str] = []
        engine = _FakeEngine(self._empty_rows_for, captured)

        # Config without ``relationships`` attribute at all.
        cfg = SimpleNamespace()

        run_phase_5(engine, cfg)  # type: ignore[arg-type]

        # The SELECT against fk_candidates is the only execute() call before
        # the early "nothing_to_do" return.  Its WHERE must carry the tier
        # predicate.
        assert len(captured) >= 1
        select_sql = captured[0].lower()
        # Filter is present and pegs tier to 'primary'.
        assert "tier" in select_sql, captured
        assert "primary" in select_sql, captured

    def test_explicit_true_emits_tier_primary_filter(self):
        captured: list[str] = []
        engine = _FakeEngine(self._empty_rows_for, captured)
        cfg = SimpleNamespace(
            relationships=SimpleNamespace(validate_only_primary_tier=True)
        )
        run_phase_5(engine, cfg)  # type: ignore[arg-type]
        assert any(
            ("tier" in sql.lower() and "primary" in sql.lower())
            for sql in captured
        )

    def test_explicit_false_omits_tier_filter(self):
        """Operators can turn the filter OFF for diagnostics / back-compat."""
        captured: list[str] = []
        engine = _FakeEngine(self._empty_rows_for, captured)
        cfg = SimpleNamespace(
            relationships=SimpleNamespace(validate_only_primary_tier=False)
        )
        run_phase_5(engine, cfg)  # type: ignore[arg-type]
        # The compiled SQL still references the ``fk_candidates`` table name,
        # but the WHERE must NOT include ``tier = 'primary'`` as a clause.
        select_sql = captured[0].lower()
        # The fk_candidates table is named in the FROM/JOIN — that's expected.
        # What we don't want is a WHERE predicate ``tier = 'primary'``.
        assert "tier = 'primary'" not in select_sql, captured

    def test_advisory_lowconf_candidates_skip_pool(self, tmp_path):
        """When the SQL row set returned to run_phase_5 contains only
        ``primary`` rows (because the WHERE filter excluded advisory ones),
        the Pool task list contains only those primary candidate_ids.

        This test simulates the post-filter result set and asserts the
        flattening into worker tasks preserves the constraint.
        """
        # Two parquet files (any valid paths so the function doesn't error
        # on path-validation; their contents are not read because the Pool
        # is patched).
        p_path = tmp_path / "parent.parquet"
        c_path = tmp_path / "child.parquet"
        pq.write_table(
            pa.table({"id": pa.array([1, 2, 3], type=pa.int64())}),
            str(p_path),
        )
        pq.write_table(
            pa.table({"pid": pa.array([1, 2], type=pa.int64())}),
            str(c_path),
        )

        # Simulate the SQL having returned only the primary row.  This
        # mirrors the post-filter behaviour: tier='primary' candidates
        # arrive; tier='advisory_lowconf' candidates are filtered upstream.
        primary_row = {
            "candidate_id": 1,
            "child_col_id": 11,
            "parent_col_id": 21,
            "source_stage": "stageA",
            "estimated_containment": 1.0,
            "name_similarity": 0.9,
            "child_column_name": "pid",
            "child_parquet_path": str(c_path),
            "parent_column_name": "id",
            "parent_parquet_path": str(p_path),
            "parent_is_pk": True,
            "parent_is_unique_indexed": True,
            "child_physical_type": "BIGINT",
            "parent_physical_type": "BIGINT",
        }

        captured_sql: list[str] = []

        def rows_for(stmt, sql):
            # Return only the primary candidate, NOT the advisory one.
            return [primary_row]

        engine = _FakeEngine(rows_for, captured_sql)

        captured_tasks: list = []

        class _FakePool:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def map(self, fn, tasks):
                # Capture for assertions.  Return one synthetic per child.
                captured_tasks.extend(list(tasks))
                results = []
                for _hdr, children, _ct in captured_tasks:
                    grp = []
                    for c in children:
                        grp.append(
                            {
                                "candidate_id": c[0],
                                "child_col_id": c[1],
                                "parent_col_id": c[2],
                                "containment_full": 0.5,  # below threshold
                                "cardinality": "NO_RELATIONSHIP",
                                "child_distinct": 2,
                                "parent_distinct": 3,
                                "orphan_count": 1,
                                "query_duration_ms": 1,
                                "source_stage": "stageA",
                                "sketch_similarity": 0.0,
                            }
                        )
                    results.append(grp)
                return results

        cfg = SimpleNamespace(
            relationships=SimpleNamespace(validate_only_primary_tier=True),
        )

        with patch("discovery.validate.multiprocessing.Pool", _FakePool), \
             patch("discovery.run_log.RunLog") as RunLogCls:
            RunLogCls.return_value = MagicMock()
            run_phase_5(engine, cfg)  # type: ignore[arg-type]

        # Exactly one task was sent: the primary candidate.
        assert len(captured_tasks) == 1, captured_tasks
        _hdr, children, _ct = captured_tasks[0]
        cand_ids = [c[0] for c in children]
        assert cand_ids == [1], cand_ids


# ---------------------------------------------------------------------------
# F1.3: succeed-after-flush race in run_phase_5
# ---------------------------------------------------------------------------


class TestPhase5SucceedAfterFlush:
    """F1.3 / E3: ``run_log.succeed`` for relationship-writing candidates
    must run AFTER ``_flush`` commits the relationships row.  If the txn
    raises, ``succeed`` MUST NOT be called for the affected candidate.
    """

    def _make_engine_returning(self, primary_rows):
        captured_sql: list[str] = []

        def rows_for(stmt, sql):
            # Only ``select`` against fk_candidates returns rows; UPDATEs
            # in txn() never happen because we patch ``txn``.
            return primary_rows

        return _FakeEngine(rows_for, captured_sql)

    def test_succeed_not_called_when_flush_raises(self, tmp_path):
        p_path = tmp_path / "parent.parquet"
        c_path = tmp_path / "child.parquet"
        pq.write_table(
            pa.table({"id": pa.array([1, 2, 3], type=pa.int64())}), str(p_path)
        )
        pq.write_table(
            pa.table({"pid": pa.array([1, 2], type=pa.int64())}), str(c_path)
        )

        primary_row = {
            "candidate_id": 7,
            "child_col_id": 17,
            "parent_col_id": 27,
            "source_stage": "stageA",
            "estimated_containment": 1.0,
            "name_similarity": 0.9,
            "child_column_name": "pid",
            "child_parquet_path": str(c_path),
            "parent_column_name": "id",
            "parent_parquet_path": str(p_path),
            "parent_is_pk": True,
            "parent_is_unique_indexed": True,
            "child_physical_type": "BIGINT",
            "parent_physical_type": "BIGINT",
        }

        engine = self._make_engine_returning([primary_row])

        class _FakePool:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def map(self, fn, tasks):
                # One above-threshold result so a relationship row would
                # be queued and a flush attempted.
                groups = []
                for _hdr, children, _ct in tasks:
                    grp = []
                    for c in children:
                        grp.append(
                            {
                                "candidate_id": c[0],
                                "child_col_id": c[1],
                                "parent_col_id": c[2],
                                "containment_full": 1.0,
                                "cardinality": "MANY_TO_ONE",
                                "child_distinct": 2,
                                "parent_distinct": 3,
                                "orphan_count": 0,
                                "query_duration_ms": 1,
                                "source_stage": "stageA",
                                "sketch_similarity": 0.0,
                            }
                        )
                    groups.append(grp)
                return groups

        # Patch ``txn`` so the flush ALWAYS raises before commit.  This
        # simulates a worker death / DB failure between succeed and write.
        from contextlib import contextmanager

        @contextmanager
        def boom_txn(_engine):
            raise RuntimeError("simulated DB failure")
            yield  # unreachable

        run_log_instance = MagicMock()

        cfg = SimpleNamespace(relationships=SimpleNamespace())

        with patch("discovery.validate.multiprocessing.Pool", _FakePool), \
             patch("discovery.run_log.RunLog", return_value=run_log_instance), \
             patch("discovery.results_db.txn", boom_txn):
            run_phase_5(engine, cfg)  # type: ignore[arg-type]

        # CRITICAL: succeed must NOT have been called for candidate_id=7,
        # because the relationships row never landed.  On resume, the
        # candidate must remain eligible for re-validation.
        assert not any(
            call.args[2] == 7 and call.args[0] == "validate"
            for call in run_log_instance.succeed.call_args_list
        ), run_log_instance.succeed.call_args_list

    def test_succeed_called_after_successful_flush(self, tmp_path):
        """The happy-path counterpart: when flush() commits, succeed is called."""
        p_path = tmp_path / "parent.parquet"
        c_path = tmp_path / "child.parquet"
        pq.write_table(
            pa.table({"id": pa.array([1, 2, 3], type=pa.int64())}), str(p_path)
        )
        pq.write_table(
            pa.table({"pid": pa.array([1, 2], type=pa.int64())}), str(c_path)
        )

        primary_row = {
            "candidate_id": 9,
            "child_col_id": 19,
            "parent_col_id": 29,
            "source_stage": "stageA",
            "estimated_containment": 1.0,
            "name_similarity": 0.9,
            "child_column_name": "pid",
            "child_parquet_path": str(c_path),
            "parent_column_name": "id",
            "parent_parquet_path": str(p_path),
            "parent_is_pk": True,
            "parent_is_unique_indexed": True,
            "child_physical_type": "BIGINT",
            "parent_physical_type": "BIGINT",
        }

        engine = self._make_engine_returning([primary_row])

        class _FakePool:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def map(self, fn, tasks):
                groups = []
                for _hdr, children, _ct in tasks:
                    groups.append(
                        [
                            {
                                "candidate_id": c[0],
                                "child_col_id": c[1],
                                "parent_col_id": c[2],
                                "containment_full": 1.0,
                                "cardinality": "MANY_TO_ONE",
                                "child_distinct": 2,
                                "parent_distinct": 3,
                                "orphan_count": 0,
                                "query_duration_ms": 1,
                                "source_stage": "stageA",
                                "sketch_similarity": 0.0,
                            }
                            for c in children
                        ]
                    )
                return groups

        from contextlib import contextmanager

        upserted = []

        class _OkConn:
            def execute(self, *a, **kw):
                upserted.append(("execute", a, kw))

        @contextmanager
        def ok_txn(_engine):
            yield _OkConn()

        run_log_instance = MagicMock()
        cfg = SimpleNamespace(relationships=SimpleNamespace())

        with patch("discovery.validate.multiprocessing.Pool", _FakePool), \
             patch("discovery.run_log.RunLog", return_value=run_log_instance), \
             patch("discovery.results_db.txn", ok_txn):
            run_phase_5(engine, cfg)  # type: ignore[arg-type]

        # Happy path: relationship was upserted AND succeed was called for
        # candidate_id=9.
        assert any(
            call.args[0] == "validate"
            and call.args[1] == "candidate"
            and call.args[2] == 9
            for call in run_log_instance.succeed.call_args_list
        ), run_log_instance.succeed.call_args_list

    def test_succeed_called_for_no_relationship_after_flush(self, tmp_path):
        """A candidate below the containment threshold writes no relationship
        but MUST still be marked succeeded so that resume skips it."""
        p_path = tmp_path / "parent.parquet"
        c_path = tmp_path / "child.parquet"
        pq.write_table(
            pa.table({"id": pa.array([1, 2, 3], type=pa.int64())}), str(p_path)
        )
        pq.write_table(
            pa.table({"pid": pa.array([1, 2], type=pa.int64())}), str(c_path)
        )

        primary_row = {
            "candidate_id": 13,
            "child_col_id": 23,
            "parent_col_id": 33,
            "source_stage": "stageA",
            "estimated_containment": 1.0,
            "name_similarity": 0.9,
            "child_column_name": "pid",
            "child_parquet_path": str(c_path),
            "parent_column_name": "id",
            "parent_parquet_path": str(p_path),
            "parent_is_pk": True,
            "parent_is_unique_indexed": True,
            "child_physical_type": "BIGINT",
            "parent_physical_type": "BIGINT",
        }

        engine = self._make_engine_returning([primary_row])

        class _FakePool:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def map(self, fn, tasks):
                # Below-threshold result → no relationship row, but must
                # still be marked succeeded.
                groups = []
                for _hdr, children, _ct in tasks:
                    groups.append(
                        [
                            {
                                "candidate_id": c[0],
                                "child_col_id": c[1],
                                "parent_col_id": c[2],
                                "containment_full": 0.0,
                                "cardinality": "NO_RELATIONSHIP",
                                "child_distinct": 2,
                                "parent_distinct": 3,
                                "orphan_count": 2,
                                "query_duration_ms": 1,
                                "source_stage": "stageA",
                                "sketch_similarity": 0.0,
                            }
                            for c in children
                        ]
                    )
                return groups

        run_log_instance = MagicMock()
        cfg = SimpleNamespace(relationships=SimpleNamespace())

        with patch("discovery.validate.multiprocessing.Pool", _FakePool), \
             patch("discovery.run_log.RunLog", return_value=run_log_instance):
            run_phase_5(engine, cfg)  # type: ignore[arg-type]

        # No-relationship case: succeed must still have been called.
        assert any(
            call.args[2] == 13 for call in run_log_instance.succeed.call_args_list
        ), run_log_instance.succeed.call_args_list


# ---------------------------------------------------------------------------
# Tier 3 #11: exact_containment_topk
# ---------------------------------------------------------------------------


class TestExactContainmentTopK:
    """Exact containment via DuckDB INTERSECT-style anti-join, top-K ranked."""

    def _write_int_parquet(self, tmp_path: Path, name: str, values: list[int]) -> Path:
        path = tmp_path / name
        pq.write_table(
            pa.table({"col": pa.array(values, type=pa.int64())}),
            str(path),
        )
        return path

    def _write_str_parquet(self, tmp_path: Path, name: str, values: list[str]) -> Path:
        path = tmp_path / name
        pq.write_table(
            pa.table({"col": pa.array(values, type=pa.string())}),
            str(path),
        )
        return path

    def test_exact_containment_topk_perfect_match(self, tmp_path: Path, con):
        """Child fully contained in one of three parents → that parent ranks
        first with containment=1.0; the others rank below 1.0."""
        # Child has values 1..50.
        child = self._write_int_parquet(
            tmp_path, "child.parquet", list(range(1, 51))
        )
        # Three parents: full superset, partial superset, disjoint set.
        parent_full = self._write_int_parquet(
            tmp_path, "p_full.parquet", list(range(1, 101))
        )
        parent_partial = self._write_int_parquet(
            tmp_path, "p_partial.parquet", list(range(1, 26)),  # only 1..25
        )
        parent_disjoint = self._write_int_parquet(
            tmp_path, "p_disjoint.parquet", list(range(1000, 1100))
        )

        ranked = exact_containment_topk(
            con,
            child_parquet=child,
            child_col="col",
            parent_candidates=[
                (parent_partial, "col"),    # idx 0 — 25/50 = 0.5
                (parent_full, "col"),       # idx 1 — 50/50 = 1.0
                (parent_disjoint, "col"),   # idx 2 — 0/50 = 0.0
            ],
            top_k=32,
        )

        # All three parents come back (top_k > n).
        assert len(ranked) == 3
        # Parent_full (idx=1) ranks first with containment=1.0.
        assert ranked[0] == (1, pytest.approx(1.0))
        # Partial second.
        assert ranked[1][0] == 0
        assert ranked[1][1] == pytest.approx(0.5, abs=1e-3)
        # Disjoint last.
        assert ranked[2] == (2, pytest.approx(0.0))

    def test_exact_containment_topk_partial(self, tmp_path: Path, con):
        """Three partial-containment parents must be ranked correctly:
        0.7 < 0.95 < 1.0."""
        # Child: 100 distinct values 1..100.
        child = self._write_int_parquet(
            tmp_path, "child_p.parquet", list(range(1, 101))
        )
        # Parent at containment 1.0 — full superset.
        p100 = self._write_int_parquet(
            tmp_path, "p100.parquet", list(range(1, 101))
        )
        # Parent at containment 0.95 — covers 95 of 100.
        p95 = self._write_int_parquet(
            tmp_path, "p95.parquet", list(range(1, 96))
        )
        # Parent at containment 0.70 — covers 70 of 100.
        p70 = self._write_int_parquet(
            tmp_path, "p70.parquet", list(range(1, 71))
        )

        ranked = exact_containment_topk(
            con,
            child_parquet=child,
            child_col="col",
            # Pass in non-sorted order so the rank check is meaningful.
            parent_candidates=[
                (p70, "col"),    # idx 0
                (p100, "col"),   # idx 1
                (p95, "col"),    # idx 2
            ],
            top_k=32,
        )

        assert len(ranked) == 3
        # Sorted DESC: 1.0 (idx 1), 0.95 (idx 2), 0.70 (idx 0).
        assert ranked[0] == (1, pytest.approx(1.0))
        assert ranked[1] == (2, pytest.approx(0.95, abs=1e-3))
        assert ranked[2] == (0, pytest.approx(0.70, abs=1e-3))
        # And the values are strictly descending.
        assert ranked[0][1] > ranked[1][1] > ranked[2][1]

    def test_exact_containment_topk_type_mismatch_skipped(
        self, tmp_path: Path, con
    ):
        """INT child vs VARCHAR parent → contributes containment=0 and the
        per-parent query is skipped (we can't measure that directly without
        instrumenting DuckDB, but the rank entry must exist with score 0)."""
        # Child: integers
        child = self._write_int_parquet(
            tmp_path, "child_int.parquet", [1, 2, 3, 4, 5]
        )
        # Parent A: VARCHAR — type-mismatched, must be 0.0.
        p_str = self._write_str_parquet(
            tmp_path, "p_str.parquet", ["1", "2", "3", "4", "5"]
        )
        # Parent B: matching INT, fully contains child.
        p_int = self._write_int_parquet(
            tmp_path, "p_int.parquet", [1, 2, 3, 4, 5, 6]
        )

        ranked = exact_containment_topk(
            con,
            child_parquet=child,
            child_col="col",
            parent_candidates=[
                (p_str, "col"),    # idx 0 — TYPE_MISMATCH → 0.0
                (p_int, "col"),    # idx 1 — full → 1.0
            ],
            top_k=32,
        )

        assert len(ranked) == 2
        # INT parent ranks first.
        assert ranked[0] == (1, pytest.approx(1.0))
        # VARCHAR parent contributes 0.0.
        assert ranked[1] == (0, pytest.approx(0.0))

    def test_exact_containment_topk_dropped_temp_table_on_error(
        self, tmp_path: Path, con
    ):
        """If any per-parent query raises, the per-call temp table must still
        be dropped so the connection is reusable for subsequent calls."""
        # Child fixture is fine.
        child = self._write_int_parquet(
            tmp_path, "child_err.parquet", [1, 2, 3, 4, 5]
        )
        # First parent: a real, valid parquet path so the table is created.
        good_parent = self._write_int_parquet(
            tmp_path, "good_parent.parquet", [1, 2, 3]
        )
        # Second parent: a path that doesn't exist on disk → DuckDB raises
        # on read_parquet().
        missing_parent = tmp_path / "does_not_exist.parquet"

        with pytest.raises(Exception):  # noqa: B017 — DuckDB raises IOException
            exact_containment_topk(
                con,
                child_parquet=child,
                child_col="col",
                parent_candidates=[
                    (good_parent, "col"),
                    (missing_parent, "col"),
                ],
                top_k=32,
            )

        # Temp table must NOT exist after the exception unwinds.
        rows = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = '_child_distinct_topk'"
        ).fetchone()
        assert rows[0] == 0, (
            "exact_containment_topk leaked its temp table after an error"
        )

        # Sanity: a fresh call on the same connection must still work.
        ranked = exact_containment_topk(
            con,
            child_parquet=child,
            child_col="col",
            parent_candidates=[(good_parent, "col")],
        )
        assert len(ranked) == 1
        # 3 of 5 distinct child values are in the good parent → 0.6.
        assert ranked[0][1] == pytest.approx(0.6, abs=1e-3)

    def test_exact_containment_topk_truncates_to_top_k(
        self, tmp_path: Path, con
    ):
        """When more parents than top_k are passed, only top_k come back."""
        child = self._write_int_parquet(
            tmp_path, "child_trunc.parquet", [1, 2, 3, 4, 5]
        )
        parents = []
        for i in range(5):
            # Each parent contains a different number of child values 1..(i+1).
            parents.append(
                (self._write_int_parquet(
                    tmp_path, f"p_t{i}.parquet", list(range(1, i + 2))
                ), "col")
            )

        ranked = exact_containment_topk(
            con, child, "col", parents, top_k=2,
        )
        assert len(ranked) == 2
        # The two highest-containment parents are the last two we built.
        idxs = [pair[0] for pair in ranked]
        assert idxs == [4, 3]

    def test_exact_containment_topk_empty_candidates(
        self, tmp_path: Path, con
    ):
        """No parent candidates → empty result, no temp table left behind."""
        child = self._write_int_parquet(
            tmp_path, "child_empty.parquet", [1, 2, 3]
        )
        ranked = exact_containment_topk(con, child, "col", [], top_k=32)
        assert ranked == []
        rows = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = '_child_distinct_topk'"
        ).fetchone()
        assert rows[0] == 0

    def test_exact_containment_topk_empty_child(self, tmp_path: Path, con):
        """Empty child distinct set → containment=0 for every parent
        (mirrors :func:`_classify`'s ``cd == 0 -> 0.0`` convention)."""
        # Child column with only NULLs.
        child_path = tmp_path / "child_nulls.parquet"
        pq.write_table(
            pa.table({"col": pa.array([None, None, None], type=pa.int64())}),
            str(child_path),
        )
        parent = self._write_int_parquet(
            tmp_path, "p_for_null.parquet", [1, 2, 3]
        )
        ranked = exact_containment_topk(
            con, child_path, "col",
            [(parent, "col"), (parent, "col")],
            top_k=32,
        )
        assert len(ranked) == 2
        for _, score in ranked:
            assert score == pytest.approx(0.0)
