"""
test_extraction.py — Unit tests for the Phase 2 extraction module.

These tests exercise the *pure* helpers — column projection logic and
pyarrow→canonical-physical-type mapping — without spinning up an extraction
service or Postgres container.

Integration coverage of the runner end-to-end (Phase 2 against testcontainers
Postgres) is provided elsewhere via the orchestrator integration tests; here
we lock in:

* C1 column-projection rules (PK always retained, STRING_LONG retained for
  PII even when not FK-eligible, fallback to ``*`` when projection ==
  full-column-set, fallback to ``*`` when feature toggle is off).
* B2 pyarrow type → canonical UPPER-CASE family mapping.
* C2 ``sample_pct`` parameter — wired into TABLESAMPLE BERNOULLI(...) only
  when ``mode='sample'``; rejected outside (0, 100].
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from discovery.extraction import (
    _DEFAULT_SAMPLE_PCT,
    _ExtractionRunner,
    _pyarrow_to_physical_type,
    _read_physical_types,
    build_select_clause,
    run_phase_2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _col(
    name: str,
    type_class: str = "INT_NARROW",
    is_fk_eligible: bool = True,
    is_pk: bool = False,
) -> dict:
    """Convenience factory for a fake col_inventory row."""
    return {
        "column_name": name,
        "type_class": type_class,
        "is_fk_eligible": is_fk_eligible,
        "is_pk": is_pk,
    }


# ---------------------------------------------------------------------------
# Column projection tests (C1)
# ---------------------------------------------------------------------------


class TestBuildSelectClause:
    def test_projection_disabled_returns_star(self) -> None:
        """When column_projection=False the clause is always ``*``."""
        cols = [_col("id", is_pk=True), _col("name", "STRING_SHORT")]
        clause, projected = build_select_clause(cols, column_projection=False)
        assert clause == "*"
        assert projected == []

    def test_empty_columns_returns_star(self) -> None:
        """No col_inventory rows → fall back to SELECT * defensively."""
        clause, projected = build_select_clause([], column_projection=True)
        assert clause == "*"
        assert projected == []

    def test_drops_string_long_when_not_pk_or_fk_eligible(self) -> None:
        """
        STRING_LONG is in the PII set so it MUST stay in the projection
        even when it's not FK-eligible — otherwise the PII pass loses
        coverage.
        """
        cols = [
            _col("id", "INT_WIDE", is_fk_eligible=True, is_pk=True),
            _col("notes", "STRING_LONG", is_fk_eligible=False, is_pk=False),
            _col("price", "FLOAT", is_fk_eligible=False, is_pk=False),
            _col("data", "BINARY", is_fk_eligible=False, is_pk=False),
        ]
        clause, projected = build_select_clause(cols, column_projection=True)
        # STRING_LONG retained (PII), price/data dropped, id kept.
        assert set(projected) == {"id", "notes"}
        assert '"id"' in clause
        assert '"notes"' in clause
        assert "price" not in clause
        assert "data" not in clause

    def test_pk_always_retained(self) -> None:
        """PK columns survive the cut even if their type_class wouldn't."""
        # A boolean PK is implausible but the rule must hold.
        cols = [
            _col("flag_pk", "BOOL", is_fk_eligible=False, is_pk=True),
            _col("x", "BOOL", is_fk_eligible=False, is_pk=False),
            _col("name", "STRING_SHORT", is_fk_eligible=True, is_pk=False),
        ]
        clause, projected = build_select_clause(cols, column_projection=True)
        # flag_pk retained because is_pk; name retained (FK-eligible); x dropped.
        assert set(projected) == {"flag_pk", "name"}
        assert "flag_pk" in clause
        assert '"flag_pk"' in clause
        assert '"name"' in clause

    def test_all_columns_projected_falls_back_to_star(self) -> None:
        """
        If the projection ends up covering *every* column we emit ``*`` to
        keep the query short and avoid drift if col_inventory is stale.
        """
        cols = [
            _col("id", "INT_WIDE", is_fk_eligible=True, is_pk=True),
            _col("name", "STRING_SHORT", is_fk_eligible=True, is_pk=False),
            _col("created_at", "TIMESTAMP", is_fk_eligible=True, is_pk=False),
        ]
        clause, projected = build_select_clause(cols, column_projection=True)
        assert clause == "*"
        assert projected == []

    def test_pii_string_short_retained(self) -> None:
        """STRING_SHORT is FK-eligible AND PII-eligible; must survive."""
        cols = [
            _col("price", "FLOAT", is_fk_eligible=False, is_pk=False),
            _col("email", "STRING_SHORT", is_fk_eligible=True, is_pk=False),
            _col("flag", "BOOL", is_fk_eligible=False, is_pk=False),
        ]
        clause, projected = build_select_clause(cols, column_projection=True)
        assert projected == ["email"]
        assert clause == '"email"'

    def test_quotes_embedded_double_quote(self) -> None:
        """Defensive identifier escaping doubles embedded ``"`` characters."""
        cols = [
            _col('weird"name', "INT_NARROW", is_fk_eligible=True, is_pk=False),
            _col("ordinary", "FLOAT", is_fk_eligible=False, is_pk=False),
        ]
        clause, projected = build_select_clause(cols, column_projection=True)
        # Weird name kept (FK-eligible); ordinary dropped; quote doubled.
        assert projected == ['weird"name']
        assert '"weird""name"' in clause

    def test_clause_preserves_order_for_deterministic_query(self) -> None:
        """Projection respects input order for stable, diff-friendly queries."""
        cols = [
            _col("a", "INT_NARROW", is_fk_eligible=True, is_pk=False),
            _col("b_drop", "FLOAT", is_fk_eligible=False, is_pk=False),
            _col("c", "STRING_SHORT", is_fk_eligible=True, is_pk=False),
        ]
        clause, projected = build_select_clause(cols, column_projection=True)
        assert projected == ["a", "c"]
        assert clause == '"a", "c"'


# ---------------------------------------------------------------------------
# pyarrow → physical_type mapping tests (B2)
# ---------------------------------------------------------------------------


class TestPyarrowToPhysicalType:
    @pytest.mark.parametrize(
        "arrow_type, expected",
        [
            (pa.bool_(), "BOOLEAN"),
            (pa.int8(), "INTEGER"),
            (pa.int16(), "INTEGER"),
            (pa.int32(), "INTEGER"),
            (pa.uint32(), "INTEGER"),
            (pa.int64(), "BIGINT"),
            (pa.uint64(), "BIGINT"),
            (pa.float16(), "REAL"),
            (pa.float32(), "REAL"),
            (pa.float64(), "DOUBLE"),
            (pa.decimal128(10, 2), "DOUBLE"),
            (pa.string(), "VARCHAR"),
            (pa.large_string(), "VARCHAR"),
            (pa.binary(), "BLOB"),
            (pa.large_binary(), "BLOB"),
            (pa.binary(8), "BLOB"),
            (pa.date32(), "DATE"),
            (pa.date64(), "DATE"),
            (pa.timestamp("us"), "TIMESTAMP"),
            (pa.timestamp("us", tz="UTC"), "TIMESTAMP"),
            (pa.time64("us"), "TIMESTAMP"),
        ],
    )
    def test_canonical_mapping(self, arrow_type: pa.DataType, expected: str) -> None:
        assert _pyarrow_to_physical_type(arrow_type) == expected

    def test_dictionary_recurses(self) -> None:
        dict_type = pa.dictionary(pa.int32(), pa.string())
        assert _pyarrow_to_physical_type(dict_type) == "VARCHAR"

    def test_unknown_falls_back_to_varchar(self) -> None:
        # List type is currently unhandled — fallback should be VARCHAR
        # so downstream physical-type comparisons treat it as string-shaped.
        list_type = pa.list_(pa.int32())
        assert _pyarrow_to_physical_type(list_type) == "VARCHAR"


class TestReadPhysicalTypes:
    def test_reads_canonical_types_from_parquet(self, tmp_path) -> None:
        """End-to-end: write a parquet, read it back via the helper."""
        path = tmp_path / "phys_test.parquet"
        table = pa.table(
            {
                "id": pa.array([1, 2, 3], type=pa.int64()),
                "small_id": pa.array([1, 2, 3], type=pa.int32()),
                "name": pa.array(["a", "b", "c"], type=pa.string()),
                "active": pa.array([True, False, True], type=pa.bool_()),
                "ratio": pa.array([1.5, 2.5, 3.5], type=pa.float32()),
            }
        )
        pq.write_table(table, str(path))

        types = _read_physical_types(str(path))
        assert types == {
            "id": "BIGINT",
            "small_id": "INTEGER",
            "name": "VARCHAR",
            "active": "BOOLEAN",
            "ratio": "REAL",
        }

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        """A non-existent parquet must NOT raise; physical-type hoist is best-effort."""
        types = _read_physical_types(str(tmp_path / "does_not_exist.parquet"))
        assert types == {}


# ---------------------------------------------------------------------------
# sample_pct wiring tests (C2 / two-pass)
# ---------------------------------------------------------------------------


def _make_runner(
    *,
    mode: str = "sample",
    sample_pct: float = _DEFAULT_SAMPLE_PCT,
) -> _ExtractionRunner:
    """
    Construct an `_ExtractionRunner` with MagicMock-stubbed dependencies.

    The runner is only used for unit-testing pure-ish helpers like
    ``_build_query`` and ``_parquet_path`` — no DB, HTTP, or threadpool
    activity is exercised.
    """
    return _ExtractionRunner(
        extraction_client=MagicMock(),
        engine=MagicMock(),
        run_log=MagicMock(),
        source_conn_config=MagicMock(),
        storage_base_path="/tmp",
        request_timeout_seconds=60,
        mode=mode,  # type: ignore[arg-type]
        sample_pct=sample_pct,
    )


class TestRunPhase2SamplePct:
    """
    Verify ``run_phase_2``'s public ``sample_pct`` parameter is wired through
    to the SQL building and validated against the (0, 100] range.
    """

    def test_signature_accepts_sample_pct(self) -> None:
        """``run_phase_2`` must declare a ``sample_pct`` keyword parameter."""
        import inspect

        sig = inspect.signature(run_phase_2)
        assert "sample_pct" in sig.parameters
        # Default lives at module scope; guard it doesn't change unannounced.
        assert sig.parameters["sample_pct"].default == _DEFAULT_SAMPLE_PCT

    def test_run_phase_2_sample_mode_default_pct(self) -> None:
        """
        With ``mode='sample'`` and no explicit ``sample_pct``, the runner's
        SQL contains ``TABLESAMPLE BERNOULLI(1)`` (1% — the default).
        """
        runner = _make_runner(mode="sample")
        query = runner._build_query("public", "users", "*")
        assert "TABLESAMPLE BERNOULLI(1)" in query

    def test_run_phase_2_sample_mode_custom_pct(self) -> None:
        """A non-default ``sample_pct=5.0`` produces ``BERNOULLI(5)`` in SQL."""
        runner = _make_runner(mode="sample", sample_pct=5.0)
        query = runner._build_query("public", "users", "*")
        assert "TABLESAMPLE BERNOULLI(5)" in query

    def test_run_phase_2_sample_mode_fractional_pct(self) -> None:
        """
        Non-integer percentages survive ``:g`` formatting verbatim
        (``2.5`` → ``BERNOULLI(2.5)``).
        """
        runner = _make_runner(mode="sample", sample_pct=2.5)
        query = runner._build_query("public", "users", "*")
        assert "TABLESAMPLE BERNOULLI(2.5)" in query

    @pytest.mark.parametrize("bad_pct", [0, -1, -1.0, 101, 200, 100.0001])
    def test_run_phase_2_invalid_pct_raises(self, bad_pct: float) -> None:
        """
        Out-of-range ``sample_pct`` values raise ``ValueError`` from
        ``run_phase_2`` (early validation, before any DB/HTTP work).
        """
        with pytest.raises(ValueError, match="sample_pct"):
            run_phase_2(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                mode="sample",
                sample_pct=bad_pct,
            )

    def test_run_phase_2_full_mode_ignores_sample_pct(self) -> None:
        """
        ``mode='full'`` does NOT emit a TABLESAMPLE clause regardless of
        the ``sample_pct`` value.
        """
        runner = _make_runner(mode="full", sample_pct=50.0)
        query = runner._build_query("public", "users", "*")
        assert "TABLESAMPLE" not in query
        assert "BERNOULLI" not in query

    def test_run_phase_2_full_subset_mode_ignores_sample_pct(self) -> None:
        """``mode='full_subset'`` likewise emits a plain SELECT — no TABLESAMPLE."""
        runner = _make_runner(mode="full_subset", sample_pct=10.0)
        query = runner._build_query("public", "users", "*")
        assert "TABLESAMPLE" not in query
        assert "BERNOULLI" not in query

    def test_run_phase_2_boundary_pct_100_accepted(self) -> None:
        """
        ``sample_pct=100`` is the upper bound — accepted (extracts every row).
        """
        runner = _make_runner(mode="sample", sample_pct=100.0)
        query = runner._build_query("public", "users", "*")
        assert "TABLESAMPLE BERNOULLI(100)" in query

    def test_runner_build_query_revalidates_pct(self) -> None:
        """
        Defence-in-depth: the runner re-validates ``sample_pct`` at SQL-build
        time so a caller who instantiates ``_ExtractionRunner`` directly
        cannot bypass the public-API validation in ``run_phase_2``.
        """
        runner = _make_runner(mode="sample", sample_pct=200.0)
        with pytest.raises(ValueError, match="sample_pct"):
            runner._build_query("public", "users", "*")
