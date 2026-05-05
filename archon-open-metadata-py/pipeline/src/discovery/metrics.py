"""
metrics.py — Prometheus metrics for the Discovery pipeline.

All metrics are module-level singletons (prometheus_client registers them
globally).  Other modules import individual metric objects and call .inc() /
.observe() / .set() directly, OR — preferably — use the typed helper
functions defined at the bottom of this module which insulate callers from
the label conventions.

Label convention
----------------
* ``status`` labels MUST use ``"succeeded"`` and ``"failed"`` (matching the
  ``run_log.status`` column).  Older code paths that emit ``"success"`` /
  ``"failure"`` are KNOWN-WRONG; downstream dashboards key off the
  ``run_log`` vocabulary, so "success"/"failure" rows silently disappear.
  See E1 (codeflow-audit.md) — orchestrator.py still emits the legacy
  labels at the time of writing.

Usage
-----
Start the HTTP server once at pipeline startup:

    from discovery.metrics import start_metrics_server, gather_pipeline_state
    start_metrics_server(port=9009, engine=engine)

Then from anywhere in the pipeline:

    from discovery.metrics import TASKS_TOTAL, TASK_DURATION_SECONDS
    TASKS_TOTAL.labels(phase="extract", status="succeeded").inc()

Metrics naming follows the Prometheus convention:
  discovery_<name>_<unit>{labels}

Recommended emission sites (per metric)
---------------------------------------
The sites below are documented intentionally so phase modules can adopt the
helpers below without re-discovering where each metric belongs:

* ``TASKS_TOTAL`` / ``TASK_DURATION_SECONDS``
    - ``orchestrator.py:_run_phase``        (per-phase wrapper, already wired)
    - ``extraction.py:_extract_one``        (per-table extract task)
    - ``fingerprint.py:_fingerprint_column`` (per-column fingerprint task)
    - ``pii_scan.py:_scan_one_column``      (per-column PII scan task)
    - ``validate.py:_validate_one_candidate`` (per-candidate validate task)

* ``ROWS_PROCESSED_TOTAL`` (counter, label=phase) — call
  :func:`record_rows_processed`:
    - ``extraction.py``  after a table is extracted (rows in manifest)
    - ``fingerprint.py`` after each row group is hashed
    - ``pii_scan.py``    after each row group is matched
    - ``validate.py``    after each candidate join completes (child rowcount)

* ``BYTES_PROCESSED_TOTAL`` (counter, label=phase) — call
  :func:`record_bytes_processed`:
    - ``extraction.py``  parquet_bytes returned in the extraction manifest
    - ``fingerprint.py`` bytes of parquet read while fingerprinting
    - ``pii_scan.py``    bytes of parquet scanned for PII

* ``PARQUET_BYTES_ON_DISK`` (gauge) — call
  :func:`update_parquet_bytes_on_disk`:
    - ``extraction.py``    after each successful extraction (running total)
    - ``orchestrator.py``  after each phase boundary (steady-state value)

* ``TABLES_PENDING`` / ``TABLES_DONE`` (gauges) — call
  :func:`update_tables_pending` and :func:`update_tables_done`, OR call
  :func:`gather_pipeline_state` to refresh both at once:
    - ``orchestrator.py:run_all`` at start (already wired via
      ``start_metrics_server(engine=engine)``)
    - ``extraction.py``  after each table transitions to ``extracted``
    - any place that calls ``inventory.run_phase_1``
"""
from __future__ import annotations

from typing import Any

import structlog
from prometheus_client import Counter, Gauge, Histogram, start_http_server

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

TASKS_TOTAL = Counter(
    "discovery_tasks_total",
    "Total number of tasks dispatched by the pipeline, labelled by phase and outcome.",
    labelnames=["phase", "status"],
)
"""
Increment after each task completes or fails.  Status MUST be one of
``"succeeded"`` or ``"failed"`` (matching ``run_log.status``):

    TASKS_TOTAL.labels(phase="fingerprint", status="succeeded").inc()
    TASKS_TOTAL.labels(phase="fingerprint", status="failed").inc()
"""

ROWS_PROCESSED_TOTAL = Counter(
    "discovery_rows_processed_total",
    "Cumulative rows processed (extracted, fingerprinted, scanned, validated).",
    labelnames=["phase"],
)
"""
    ROWS_PROCESSED_TOTAL.labels(phase="extract").inc(row_count)

Prefer :func:`record_rows_processed` for safe ``n=0`` and negative-guard.
"""

BYTES_PROCESSED_TOTAL = Counter(
    "discovery_bytes_processed_total",
    "Cumulative bytes processed (Parquet bytes read or written).",
    labelnames=["phase"],
)
"""
    BYTES_PROCESSED_TOTAL.labels(phase="extract").inc(byte_count)

Prefer :func:`record_bytes_processed` for safe ``n=0`` and negative-guard.
"""

# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------

PARQUET_BYTES_ON_DISK = Gauge(
    "discovery_parquet_bytes_on_disk",
    "Current total size in bytes of all Parquet files under storage.base_path.",
)
"""
Poll and set periodically — the coordinator updates this after each extraction:

    PARQUET_BYTES_ON_DISK.set(total_parquet_bytes)

Prefer :func:`update_parquet_bytes_on_disk`.
"""

TABLES_PENDING = Gauge(
    "discovery_tables_pending",
    "Number of tables in tbl_inventory with status='pending'.",
)
"""
Prefer :func:`update_tables_pending` or :func:`gather_pipeline_state`.
"""

TABLES_DONE = Gauge(
    "discovery_tables_done",
    "Number of tables in tbl_inventory with status='extracted' or 'analyzed'.",
)
"""
Prefer :func:`update_tables_done` or :func:`gather_pipeline_state`.
"""

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------

TASK_DURATION_SECONDS = Histogram(
    "discovery_task_duration_seconds",
    "Wall-clock seconds taken to complete one pipeline task.",
    labelnames=["phase"],
    # Buckets span from 100ms to ~2 hours; extraction tasks are long-tail.
    buckets=(
        0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0,
        60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0, 7200.0,
    ),
)
"""
Use as a context manager:

    with TASK_DURATION_SECONDS.labels(phase="fingerprint").time():
        do_work()

Or time manually:

    import time
    start = time.monotonic()
    do_work()
    TASK_DURATION_SECONDS.labels(phase="validate").observe(time.monotonic() - start)
"""

# Backwards-compatibility alias — earlier code imported the histogram as
# ``TASK_DURATION``.  Keep the alias indefinitely so existing imports do not
# break; new code SHOULD use ``TASK_DURATION_SECONDS`` to match the metric's
# exposed name on the Prometheus endpoint.
TASK_DURATION = TASK_DURATION_SECONDS


# ---------------------------------------------------------------------------
# Typed helpers — preferred call sites for phase modules
# ---------------------------------------------------------------------------


def record_rows_processed(phase: str, n: int) -> None:
    """
    Increment :data:`ROWS_PROCESSED_TOTAL` for *phase* by *n* rows.

    Silently skips ``n <= 0`` so callers can pass through manifest values
    without having to special-case empty extracts.

    Parameters
    ----------
    phase:
        One of ``"extract"``, ``"fingerprint"``, ``"pii_scan"``, ``"validate"``.
    n:
        Number of rows processed in this batch.  ``0`` and negative values
        are silently ignored (Prometheus counters are monotonic).
    """
    if n <= 0:
        return
    ROWS_PROCESSED_TOTAL.labels(phase=phase).inc(n)


def record_bytes_processed(phase: str, n: int) -> None:
    """
    Increment :data:`BYTES_PROCESSED_TOTAL` for *phase* by *n* bytes.

    Silently skips ``n <= 0`` (see :func:`record_rows_processed`).
    """
    if n <= 0:
        return
    BYTES_PROCESSED_TOTAL.labels(phase=phase).inc(n)


def update_parquet_bytes_on_disk(bytes_total: int) -> None:
    """
    Set :data:`PARQUET_BYTES_ON_DISK` to *bytes_total*.

    The metric is a snapshot, so this is a ``.set()`` — call after each
    extraction or at any other point you have a reliable total.
    """
    if bytes_total < 0:
        bytes_total = 0
    PARQUET_BYTES_ON_DISK.set(bytes_total)


def update_tables_pending(n: int) -> None:
    """Set :data:`TABLES_PENDING` to *n*."""
    if n < 0:
        n = 0
    TABLES_PENDING.set(n)


def update_tables_done(n: int) -> None:
    """Set :data:`TABLES_DONE` to *n*."""
    if n < 0:
        n = 0
    TABLES_DONE.set(n)


def gather_pipeline_state(engine: Any) -> dict[str, int]:
    """
    Refresh the inventory-state gauges from ``tbl_inventory.status``.

    Runs a single GROUP BY query, then updates :data:`TABLES_PENDING` and
    :data:`TABLES_DONE`.  ``"extracted"`` and ``"analyzed"`` both count
    toward TABLES_DONE; ``"excluded"`` is reported in the returned mapping
    but does not update any gauge (it's neither pending nor done).

    Errors are logged and swallowed — observability must never crash the
    pipeline.

    Parameters
    ----------
    engine:
        SQLAlchemy engine connected to the results DB.  Pass ``None`` to
        skip (helper returns an empty dict).

    Returns
    -------
    dict[str, int]
        Mapping of ``status -> row_count`` from ``tbl_inventory``.  Empty
        if the engine is None or the query fails.
    """
    if engine is None:
        return {}
    try:
        from sqlalchemy import func, select  # noqa: PLC0415

        from discovery.results_db import tbl_inventory_t  # noqa: PLC0415

        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    tbl_inventory_t.c.status,
                    func.count().label("cnt"),
                ).group_by(tbl_inventory_t.c.status)
            ).all()
        counts: dict[str, int] = {row.status: int(row.cnt) for row in rows}
    except Exception as exc:  # pragma: no cover - defence-in-depth
        log.warning("gather_pipeline_state_failed", error=str(exc))
        return {}

    pending = counts.get("pending", 0)
    done = counts.get("extracted", 0) + counts.get("analyzed", 0)
    update_tables_pending(pending)
    update_tables_done(done)
    log.info(
        "pipeline_state_gauges_refreshed",
        pending=pending,
        done=done,
        excluded=counts.get("excluded", 0),
    )
    return counts


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def start_metrics_server(port: int = 9009, engine: Any | None = None) -> None:
    """
    Start the Prometheus HTTP scrape endpoint on *port*.

    This is a thin wrapper around ``prometheus_client.start_http_server`` that
    adds structured logging and gracefully ignores repeated calls (idempotent
    within a process — the underlying server raises ``OSError: address already
    in use`` on re-bind, which we catch and warn on).

    If *engine* is supplied, :func:`gather_pipeline_state` is called once at
    startup so the inventory gauges have meaningful initial values.

    Parameters
    ----------
    port:
        TCP port to listen on.  Defaults to 9009, matching the config schema.
        Override via ``config.metrics.port`` before calling.
    engine:
        Optional SQLAlchemy engine connected to the results DB.  When
        provided, the inventory state gauges are seeded before the HTTP
        server returns.
    """
    try:
        start_http_server(port)
        log.info("metrics_server_started", port=port)
    except OSError as exc:
        # Address already in use — another worker or a prior call already
        # started the server; this is harmless in a single-process pipeline
        # but worth a warning for visibility.
        log.warning(
            "metrics_server_already_bound",
            port=port,
            error=str(exc),
        )

    if engine is not None:
        gather_pipeline_state(engine)
