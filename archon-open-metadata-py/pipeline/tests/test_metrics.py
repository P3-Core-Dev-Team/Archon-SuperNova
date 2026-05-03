"""
test_metrics.py — Smoke tests for discovery.metrics.

These tests verify:
  * the module imports without side effects;
  * the documented metric singletons exist with the expected types;
  * each labelled metric accepts ``.inc()`` / ``.observe()`` without raising;
  * ``start_metrics_server`` is exposed.

The tests deliberately do NOT call ``start_metrics_server`` — binding a TCP
port from inside the test runner is flaky (parallel runs, CI port reuse).
The function's only error path (``OSError: address already in use``) is
caught and logged inside the helper, so we trust it to behave.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from discovery import metrics


# ---------------------------------------------------------------------------
# Existence + type checks
# ---------------------------------------------------------------------------


def test_tasks_total_is_counter() -> None:
    assert isinstance(metrics.TASKS_TOTAL, Counter)


def test_rows_processed_total_is_counter() -> None:
    assert isinstance(metrics.ROWS_PROCESSED_TOTAL, Counter)


def test_bytes_processed_total_is_counter() -> None:
    assert isinstance(metrics.BYTES_PROCESSED_TOTAL, Counter)


def test_parquet_bytes_on_disk_is_gauge() -> None:
    assert isinstance(metrics.PARQUET_BYTES_ON_DISK, Gauge)


def test_tables_pending_is_gauge() -> None:
    assert isinstance(metrics.TABLES_PENDING, Gauge)


def test_tables_done_is_gauge() -> None:
    assert isinstance(metrics.TABLES_DONE, Gauge)


def test_task_duration_seconds_is_histogram() -> None:
    assert isinstance(metrics.TASK_DURATION_SECONDS, Histogram)


def test_task_duration_alias() -> None:
    """Backwards-compatible alias for the renamed histogram."""
    assert metrics.TASK_DURATION is metrics.TASK_DURATION_SECONDS


def test_start_metrics_server_callable() -> None:
    assert callable(metrics.start_metrics_server)


# ---------------------------------------------------------------------------
# Behaviour: labelled metrics accept the documented call sites
# ---------------------------------------------------------------------------


def test_tasks_total_inc_succeeds() -> None:
    """
    Canonical status labels are 'succeeded' / 'failed' — matching
    run_log.status.  Older orchestrator code emits 'success' / 'failure';
    those labels still work (Prometheus accepts any string), they're just
    misaligned with run_log and dashboards keyed off the run_log vocabulary.
    """
    metrics.TASKS_TOTAL.labels(phase="inventory", status="succeeded").inc()
    metrics.TASKS_TOTAL.labels(phase="extract", status="failed").inc()


def test_rows_processed_total_inc_succeeds() -> None:
    metrics.ROWS_PROCESSED_TOTAL.labels(phase="extract").inc(123)


def test_bytes_processed_total_inc_succeeds() -> None:
    metrics.BYTES_PROCESSED_TOTAL.labels(phase="extract").inc(456)


def test_task_duration_time_context_manager() -> None:
    """Histogram .time() context manager is the documented call form."""
    with metrics.TASK_DURATION_SECONDS.labels(phase="fingerprint").time():
        pass


def test_gauges_set_and_inc() -> None:
    metrics.PARQUET_BYTES_ON_DISK.set(0)
    metrics.TABLES_PENDING.set(5)
    metrics.TABLES_DONE.inc()
