"""
test_metrics_helpers.py — Smoke tests for the typed helper layer added to
``discovery.metrics`` as part of the E1 hygiene fix.

The helpers wrap the bare Counter/Gauge objects so phase modules don't have
to know the label names.  These tests verify the helpers are callable, do
not raise on edge inputs (n=0, n<0), and delegate to the underlying metric
objects correctly.

``gather_pipeline_state`` is exercised in two modes:
  * with ``engine=None``     — silent skip path
  * with a stub engine that throws — error path returns ``{}``

A real-DB integration is intentionally out of scope here; the
``test_results_db_*`` integration tests cover the SQLAlchemy round-trip.
"""
from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Gauge

from discovery import metrics


# ---------------------------------------------------------------------------
# Counter helpers
# ---------------------------------------------------------------------------


def _counter_value(counter: Counter, **labels: str) -> float:
    """Read the current value of a labelled prometheus Counter."""
    return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]


def test_record_rows_processed_increments() -> None:
    before = _counter_value(metrics.ROWS_PROCESSED_TOTAL, phase="extract")
    metrics.record_rows_processed("extract", 100)
    after = _counter_value(metrics.ROWS_PROCESSED_TOTAL, phase="extract")
    assert after - before == 100


def test_record_rows_processed_zero_is_noop() -> None:
    before = _counter_value(metrics.ROWS_PROCESSED_TOTAL, phase="fingerprint")
    metrics.record_rows_processed("fingerprint", 0)
    metrics.record_rows_processed("fingerprint", -5)
    after = _counter_value(metrics.ROWS_PROCESSED_TOTAL, phase="fingerprint")
    assert after == before


def test_record_bytes_processed_increments() -> None:
    before = _counter_value(metrics.BYTES_PROCESSED_TOTAL, phase="pii_scan")
    metrics.record_bytes_processed("pii_scan", 4096)
    after = _counter_value(metrics.BYTES_PROCESSED_TOTAL, phase="pii_scan")
    assert after - before == 4096


def test_record_bytes_processed_zero_is_noop() -> None:
    before = _counter_value(metrics.BYTES_PROCESSED_TOTAL, phase="validate")
    metrics.record_bytes_processed("validate", 0)
    metrics.record_bytes_processed("validate", -1)
    after = _counter_value(metrics.BYTES_PROCESSED_TOTAL, phase="validate")
    assert after == before


# ---------------------------------------------------------------------------
# Gauge helpers
# ---------------------------------------------------------------------------


def _gauge_value(gauge: Gauge) -> float:
    return gauge._value.get()  # type: ignore[attr-defined]


def test_update_parquet_bytes_on_disk_sets() -> None:
    metrics.update_parquet_bytes_on_disk(123_456)
    assert _gauge_value(metrics.PARQUET_BYTES_ON_DISK) == 123_456


def test_update_parquet_bytes_on_disk_negative_clamps_to_zero() -> None:
    metrics.update_parquet_bytes_on_disk(-7)
    assert _gauge_value(metrics.PARQUET_BYTES_ON_DISK) == 0


def test_update_tables_pending_sets() -> None:
    metrics.update_tables_pending(42)
    assert _gauge_value(metrics.TABLES_PENDING) == 42


def test_update_tables_pending_negative_clamps() -> None:
    metrics.update_tables_pending(-1)
    assert _gauge_value(metrics.TABLES_PENDING) == 0


def test_update_tables_done_sets() -> None:
    metrics.update_tables_done(11)
    assert _gauge_value(metrics.TABLES_DONE) == 11


def test_update_tables_done_negative_clamps() -> None:
    metrics.update_tables_done(-3)
    assert _gauge_value(metrics.TABLES_DONE) == 0


# ---------------------------------------------------------------------------
# gather_pipeline_state
# ---------------------------------------------------------------------------


def test_gather_pipeline_state_none_engine_returns_empty() -> None:
    assert metrics.gather_pipeline_state(None) == {}


def test_gather_pipeline_state_swallows_errors() -> None:
    """A broken engine must not crash the pipeline; helper returns {}."""

    class _BoomEngine:
        def connect(self) -> Any:
            raise RuntimeError("intentional test failure")

    result = metrics.gather_pipeline_state(_BoomEngine())
    assert result == {}


def test_gather_pipeline_state_updates_gauges() -> None:
    """When the query succeeds, both gauges are refreshed.

    Uses an in-memory stub engine that mimics SQLAlchemy enough to satisfy
    ``gather_pipeline_state`` (a ``connect()`` context manager whose
    ``execute`` returns a row sequence).
    """
    from contextlib import contextmanager

    class _Row:
        def __init__(self, status: str, cnt: int) -> None:
            self.status = status
            self.cnt = cnt

    class _Result:
        def __init__(self, rows: list[_Row]) -> None:
            self._rows = rows

        def all(self) -> list[_Row]:
            return list(self._rows)

    class _Conn:
        def __init__(self, rows: list[_Row]) -> None:
            self._rows = rows

        def execute(self, *_a: Any, **_k: Any) -> _Result:
            return _Result(self._rows)

    class _StubEngine:
        def __init__(self, rows: list[_Row]) -> None:
            self._rows = rows

        @contextmanager
        def connect(self):
            yield _Conn(self._rows)

    rows = [
        _Row("pending", 7),
        _Row("extracted", 3),
        _Row("analyzed", 2),
        _Row("excluded", 1),
    ]
    counts = metrics.gather_pipeline_state(_StubEngine(rows))
    assert counts == {"pending": 7, "extracted": 3, "analyzed": 2, "excluded": 1}
    assert _gauge_value(metrics.TABLES_PENDING) == 7
    assert _gauge_value(metrics.TABLES_DONE) == 3 + 2  # extracted + analyzed
