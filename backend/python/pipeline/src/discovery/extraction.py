"""
extraction.py — Phase 2: Full Extraction.

For each non-excluded table in tbl_inventory, submit an extraction request to
the Spring Boot extraction service.  Results (Parquet path, bytes, row count)
are written back to tbl_inventory.

Concurrency model:
  - Worker pool of N (default 8, configurable via orchestration.workers.extract)
  - Uses concurrent.futures.ThreadPoolExecutor — threads are fine since the
    bottleneck is I/O (HTTP waiting for Spring Boot to stream data)
  - run_log provides per-table resumability: tables with status='succeeded'
    are skipped without re-submitting

Idempotency:
  - tbl_inventory.parquet_path / parquet_bytes / extracted_at are upserted
  - Parquet files are written at deterministic paths (schema__table.parquet
    for full extracts; schema__table.sample.parquet for sampled extracts) so
    re-extraction overwrites the old file without colliding across modes.

Column projection (C1 / Quick-win Q1 in minimal-scan):
  Instead of ``SELECT *``, the runner consults col_inventory and emits a
  column list containing only:
    * is_fk_eligible columns (drop STRING_LONG/FLOAT/BOOL/BINARY for FK use)
    * STRING_SHORT and STRING_LONG columns (PII scanning needs both)
    * primary-key columns (always retained regardless of eligibility)
  Falls back to ``SELECT *`` when the projection covers every column or when
  no col_inventory rows are available (defence-in-depth).

Two-pass extraction (C2 in minimal-scan):
  ``run_phase_2(..., mode=..., sample_pct=...)`` accepts:
    * ``'full'``        (default) — full extract per table, deterministic
                        path, mark_extracted state transitions.
    * ``'sample'``      — TABLESAMPLE BERNOULLI(``sample_pct``) per table
                        (default 1.0 = 1%); writes to a ``.sample.parquet``
                        sibling path.  Does NOT call mark_extracted —
                        the parquet is for triage only.  ``sample_pct``
                        must lie in the half-open range (0, 100].
    * ``'full_subset'`` — full extract restricted to a caller-supplied list
                        of table_ids (used by the orchestrator after Phase 4
                        identifies survivors).

Physical-type hoist (B2):
  After a successful full extract, read the parquet schema via
  ``pyarrow.parquet.read_schema`` (cheap — metadata only) and write a
  canonical UPPER-CASE family string into ``col_inventory.physical_type``.
  Eliminates two DESCRIBE round-trips per candidate in Phase 5 (validate.py).

Security: no direct source DB connections.  All extraction goes through the
extraction service.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

import pyarrow as pa
import pyarrow.parquet as pq
import structlog
from sqlalchemy import select
from sqlalchemy.engine import Engine

from discovery.extraction_client import ExtractionClient
from discovery.models import (
    ConnectionConfig,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResponse,
    OutputConfig,
)
from discovery.results_db import (
    ColInventory,
    TblInventory,
    col_inventory_t,
    tbl_inventory_t,
    txn,
)
from discovery.run_log import RunLog

log = structlog.get_logger("discovery.extraction")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_WORKERS = 8
_DEFAULT_SAMPLE_PCT = 1.0  # TABLESAMPLE BERNOULLI(1) — 1% of rows

# Type-class buckets that should *always* survive column-projection pruning.
# STRING_SHORT and STRING_LONG carry PII signal; STRING_LONG is also the only
# PII-eligible class that is NOT FK-eligible, so we list both explicitly
# rather than relying on is_fk_eligible to cover them.
_PII_TYPE_CLASSES = frozenset({"STRING_SHORT", "STRING_LONG"})


ExtractionMode = Literal["full", "sample", "full_subset"]


# ---------------------------------------------------------------------------
# Pure helpers (testable without an engine)
# ---------------------------------------------------------------------------


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier, doubling embedded double-quotes for safety."""
    return '"' + str(name).replace('"', '""') + '"'


def build_select_clause(
    columns: list[dict[str, Any]],
    column_projection: bool = True,
) -> tuple[str, list[str]]:
    """
    Build the column list for the extraction SELECT.

    Pure helper — accepts a list of col_inventory dicts (each carrying at
    least ``column_name``, ``type_class``, ``is_fk_eligible``, ``is_pk``)
    plus a feature toggle, and returns ``(clause, projected_columns)`` where
    ``clause`` is the literal SQL fragment to plug after ``SELECT`` and
    ``projected_columns`` is the column-name list (empty list signals
    fallback to ``*``).

    Selection rule (C1):
      * Always include the column when ``is_pk`` is true.
      * Otherwise include if ``is_fk_eligible`` is true.
      * Otherwise include if ``type_class`` is STRING_SHORT or STRING_LONG
        (PII scanning needs the full range of string columns, including
        STRING_LONG which is NOT FK-eligible).

    Falls back to ``"*"`` (and an empty projected list) when:
      * ``column_projection`` is False (legacy SELECT * mode), OR
      * the columns list is empty (defence-in-depth — better to over-extract
        than to risk an empty SELECT), OR
      * the projection ends up covering every column anyway (no point
        emitting the explicit list — keeps the query short).

    The returned clause is ready to interpolate after ``SELECT``; column
    names are quoted with double quotes and embedded quotes are escaped.
    """
    if not column_projection:
        return "*", []
    if not columns:
        return "*", []

    projected: list[str] = []
    for col in columns:
        col_name = col.get("column_name")
        if not col_name:
            continue
        type_class = col.get("type_class")
        is_pk = bool(col.get("is_pk"))
        is_fk_eligible = bool(col.get("is_fk_eligible"))
        is_pii_eligible = type_class in _PII_TYPE_CLASSES

        if is_pk or is_fk_eligible or is_pii_eligible:
            projected.append(str(col_name))

    # If we'd select every column anyway, fall back to '*' for short queries
    # and to dodge any edge case where col_inventory is missing a row that
    # actually exists in the source schema.
    if not projected or len(projected) == len(columns):
        return "*", []

    clause = ", ".join(_quote_ident(name) for name in projected)
    return clause, projected


def _pyarrow_to_physical_type(arrow_type: pa.DataType) -> str:
    """
    Map a pyarrow DataType to a canonical UPPER-CASE physical-type family.

    The canonical set used downstream (col_inventory.physical_type and
    validate.py's _PHYS_TYPE_FAMILY) is:

        INTEGER  — int8/int16/int32 (and unsigned counterparts)
        BIGINT   — int64 / uint64
        VARCHAR  — string / large_string / utf8 / fixed-size string / dict<…>
        BOOLEAN  — bool
        DATE     — date32 / date64
        TIMESTAMP — timestamp / time64 (anything time-shaped)
        DOUBLE   — float64 / decimal*
        REAL     — float32 / float16
        BLOB     — binary / large_binary / fixed-size binary

    Anything that doesn't match a known family falls back to ``VARCHAR`` —
    string is the safest assumption because the extraction service writes
    unrecognised types as strings (see mock_extraction_service._resolve_pg_to_arrow).
    """
    # Booleans first — pyarrow.types.is_integer doesn't catch them but they
    # are technically integer-flavoured in some Arrow contexts.
    if pa.types.is_boolean(arrow_type):
        return "BOOLEAN"

    # Integer family — split BIGINT vs INTEGER on width.
    if pa.types.is_int64(arrow_type) or pa.types.is_uint64(arrow_type):
        return "BIGINT"
    if pa.types.is_integer(arrow_type):
        return "INTEGER"

    # Floats — DOUBLE for 64-bit, REAL for 32-bit, DOUBLE for decimals.
    if pa.types.is_float64(arrow_type):
        return "DOUBLE"
    if pa.types.is_float32(arrow_type) or pa.types.is_float16(arrow_type):
        return "REAL"
    if pa.types.is_decimal(arrow_type):
        return "DOUBLE"
    if pa.types.is_floating(arrow_type):
        return "DOUBLE"

    # Date / timestamp / time.
    if pa.types.is_date(arrow_type):
        return "DATE"
    if pa.types.is_timestamp(arrow_type) or pa.types.is_time(arrow_type):
        return "TIMESTAMP"

    # Binary BEFORE string check — large_binary is binary-flavoured.
    if pa.types.is_binary(arrow_type) or pa.types.is_large_binary(arrow_type):
        return "BLOB"
    if pa.types.is_fixed_size_binary(arrow_type):
        return "BLOB"

    # Strings (covers utf8, large_utf8, fixed-size string, dict<string>).
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return "VARCHAR"
    if pa.types.is_dictionary(arrow_type):
        # Dictionary-encoded — recurse on the value type.
        return _pyarrow_to_physical_type(arrow_type.value_type)

    # Default — UUID, JSON, lists, nested types all fall through to VARCHAR.
    return "VARCHAR"


def _read_physical_types(parquet_path: str) -> dict[str, str]:
    """
    Read a parquet file's schema (metadata only) and return the canonical
    physical-type family per column.

    Returns an empty dict on failure — extraction must succeed even if the
    physical-type hoist can't read the parquet (validation falls back to
    DESCRIBE).
    """
    try:
        schema = pq.read_schema(parquet_path)
    except Exception as exc:  # pragma: no cover - defence-in-depth
        log.warning(
            "extraction.physical_type_read_failed",
            parquet_path=parquet_path,
            error=str(exc),
        )
        return {}
    out: dict[str, str] = {}
    for field in schema:
        out[field.name] = _pyarrow_to_physical_type(field.type)
    return out


# ---------------------------------------------------------------------------
# Internal runner
# ---------------------------------------------------------------------------


class _ExtractionRunner:
    """
    Internal helper for Phase 2.  Public surface is :func:`run_phase_2`.
    """

    def __init__(
        self,
        extraction_client: ExtractionClient,
        engine: Engine,
        run_log: RunLog,
        source_conn_config: ConnectionConfig,
        storage_base_path: str,
        request_timeout_seconds: int,
        workers: int = _DEFAULT_WORKERS,
        limit: int | None = None,
        mode: ExtractionMode = "full",
        table_ids: Iterable[int] | None = None,
        column_projection: bool = True,
        sample_pct: float = _DEFAULT_SAMPLE_PCT,
    ) -> None:
        self._client = extraction_client
        self._engine = engine
        self._run_log = run_log
        self._conn_config = source_conn_config
        self._base_path = Path(storage_base_path)
        self._timeout = request_timeout_seconds
        self._workers = workers
        self._limit = limit
        self._mode: ExtractionMode = mode
        self._table_ids: set[int] | None = (
            {int(t) for t in table_ids} if table_ids is not None else None
        )
        self._column_projection = column_projection
        self._sample_pct = float(sample_pct)

    def run(self) -> None:
        """
        Extract all pending non-excluded tables using a thread pool.

        Already-succeeded tables are skipped (full mode only — sample/subset
        re-extract every time because they are typically driven by the
        orchestrator after a triage step).  Failed tables from a previous
        run are retried.
        """
        tables = self._fetch_pending_tables()
        if self._limit is not None:
            tables = tables[: self._limit]

        log.info(
            "extraction.start",
            total_tables=len(tables),
            workers=self._workers,
            mode=self._mode,
            column_projection=self._column_projection,
        )

        succeeded = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            future_to_table: dict[Future[None], dict[str, Any]] = {
                pool.submit(self._extract_table_task, tbl): tbl for tbl in tables
            }

            for future in as_completed(future_to_table):
                tbl = future_to_table[future]
                exc = future.exception()
                if exc is not None:
                    failed += 1
                    log.error(
                        "extraction.table_failed",
                        schema=tbl["schema_name"],
                        table=tbl["table_name"],
                        table_id=tbl["table_id"],
                        mode=self._mode,
                        error=str(exc),
                    )
                else:
                    succeeded += 1
                    log.info(
                        "extraction.table_done",
                        schema=tbl["schema_name"],
                        table=tbl["table_name"],
                        table_id=tbl["table_id"],
                        mode=self._mode,
                    )

        log.info(
            "extraction.complete",
            succeeded=succeeded,
            failed=failed,
            total=len(tables),
            mode=self._mode,
        )

        if failed > 0 and succeeded == 0:
            raise RuntimeError(
                f"Extraction phase: all {failed} tables failed (see run_log)"
            )

    # ------------------------------------------------------------------
    # Per-table task
    # ------------------------------------------------------------------

    def _extract_table_task(self, table_meta: dict[str, Any]) -> None:
        """Extract a single table.  Called from a worker thread."""
        table_id: int = table_meta["table_id"]
        schema_name: str = table_meta["schema_name"]
        table_name: str = table_meta["table_name"]

        # Per-table run_log resume guard.  Sample / full_subset modes do NOT
        # consult the run_log: they always re-extract because they are driven
        # by an orchestrator step that has already decided this table is
        # in scope (e.g. a sample triage pass, or a Pass-2 retake of survivors).
        if self._mode == "full" and self._run_log.is_complete(
            "extract", "table", table_id
        ):
            log.debug(
                "extraction.skip_complete",
                schema=schema_name,
                table=table_name,
                table_id=table_id,
            )
            return

        if self._mode == "full":
            self._run_log.start("extract", "table", table_id)

        try:
            parquet_path = str(self._parquet_path(schema_name, table_name))
            Path(parquet_path).parent.mkdir(parents=True, exist_ok=True)

            # Build the SELECT clause from col_inventory (C1).  The query
            # constructor also applies TABLESAMPLE for sample mode (C2).
            select_clause, projected = self._build_select_clause(table_id)
            query = self._build_query(
                schema_name, table_name, select_clause
            )

            request = ExtractionRequest(
                connection=self._conn_config,
                query=query,
                output=OutputConfig(
                    path=parquet_path,
                    compression="zstd",
                    compression_level=3,
                ),
                options=ExtractionOptions(
                    fetch_size=10_000,
                    timeout_seconds=self._timeout,
                    tag=f"phase=2,table_id={table_id},mode={self._mode}",
                ),
            )

            log.info(
                "extraction.submitting",
                schema=schema_name,
                table=table_name,
                table_id=table_id,
                mode=self._mode,
                projected_cols=len(projected),
                projection_active=bool(projected),
            )

            response: ExtractionResponse = self._client.extract_sync(request)

            total_rows = response.manifest.total_rows if response.manifest else None
            total_bytes = response.manifest.total_bytes if response.manifest else None
            actual_path = (
                response.manifest.files[0].path
                if response.manifest and response.manifest.files
                else parquet_path
            )

            # mark_extracted persists the canonical parquet_path that
            # downstream phases (fingerprint.py, validate.py) read from
            # tbl_inventory.  Both 'full' and 'full_subset' write a full
            # parquet file at the canonical path and therefore SHOULD update
            # the DB so subsequent phases can find it.  'sample' writes to a
            # ``.sample.parquet`` sibling that is not a full extract — it
            # must NOT trample the canonical parquet_path.
            if self._mode in {"full", "full_subset"}:
                with txn(self._engine) as conn:
                    TblInventory(conn).mark_extracted(
                        schema_name=schema_name,
                        table_name=table_name,
                        parquet_path=actual_path,
                        parquet_bytes=total_bytes,
                        row_count_estimate=total_rows,
                        extracted_at=datetime.now(timezone.utc),
                    )

            # Hoist physical_type out of validate.py (B2).  Read the parquet
            # schema once (metadata only) and persist per column.  Skip on
            # sample mode — the sample parquet doesn't represent the real
            # schema breadth (when column projection is active some columns
            # are missing, and in full_subset / full we want one canonical
            # type per col_inventory row).
            if self._mode in {"full", "full_subset"}:
                phys_types = _read_physical_types(actual_path)
                if phys_types:
                    with txn(self._engine) as conn:
                        ColInventory(conn).update_physical_types(
                            table_id=table_id, types_by_column=phys_types
                        )
                    log.debug(
                        "extraction.physical_type_persisted",
                        schema=schema_name,
                        table=table_name,
                        table_id=table_id,
                        n_columns=len(phys_types),
                    )

            if self._mode == "full":
                self._run_log.succeed("extract", "table", table_id)

        except Exception as exc:
            err_msg = str(exc)
            if self._mode == "full":
                self._run_log.fail("extract", "table", table_id, err_msg)
            raise

    # ------------------------------------------------------------------
    # Query construction helpers
    # ------------------------------------------------------------------

    def _parquet_path(self, schema_name: str, table_name: str) -> Path:
        """
        Resolve the parquet output path for the active mode.

        ``full`` and ``full_subset`` both write to the canonical
        ``<schema>__<table>.parquet`` (a Pass-2 retake overwrites the
        Pass-1 sample's full sibling, which is the desired behaviour).
        ``sample`` writes to a ``<schema>__<table>.sample.parquet``
        sibling so a sampled triage pass never trashes a prior full extract.
        """
        if self._mode == "sample":
            return self._base_path / f"{schema_name}__{table_name}.sample.parquet"
        return self._base_path / f"{schema_name}__{table_name}.parquet"

    def _build_select_clause(self, table_id: int) -> tuple[str, list[str]]:
        """
        Read col_inventory for *table_id* and build the column list.

        Returns ``(clause, projected_column_names)``; an empty
        projected_column_names signals fallback to ``SELECT *``.
        """
        cols = self._fetch_columns_for_table(table_id)
        return build_select_clause(cols, column_projection=self._column_projection)

    def _build_query(
        self, schema_name: str, table_name: str, select_clause: str
    ) -> str:
        """
        Compose the final query.  The schema/table identifiers come from
        information_schema and are validated upstream (by inventory's
        ``_validate_schema_name``); we double-quote them defensively.
        """
        base = (
            f"SELECT {select_clause} "
            f'FROM {_quote_ident(schema_name)}.{_quote_ident(table_name)}'
        )
        if self._mode == "sample":
            # BERNOULLI(percent) — row-level Bernoulli sample, the only
            # method safe against page-clustered fact tables.  Spec'd by
            # the JSqlParser whitelist (constant parameter, no joins).
            #
            # Defence-in-depth: re-validate sample_pct here even though
            # run_phase_2 already does — the runner constructor accepts
            # arbitrary floats from the caller, and the value is being
            # interpolated into raw SQL.  Reject anything outside (0, 100].
            pct = float(self._sample_pct)
            if not (0.0 < pct <= 100.0):
                raise ValueError(
                    f"sample_pct must be in (0, 100]; got {pct!r}"
                )
            # ``:g`` strips trailing zeros (1.0 -> '1', 5.0 -> '5', 2.5 -> '2.5')
            # so the emitted SQL stays readable and stable for diffing.
            base += f" TABLESAMPLE BERNOULLI({pct:g})"
        return base

    def _fetch_columns_for_table(self, table_id: int) -> list[dict[str, Any]]:
        """Fetch ``column_name``, ``type_class``, ``is_fk_eligible``, ``is_pk``."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    col_inventory_t.c.column_name,
                    col_inventory_t.c.type_class,
                    col_inventory_t.c.is_fk_eligible,
                    col_inventory_t.c.is_pk,
                )
                .where(col_inventory_t.c.table_id == table_id)
                .order_by(col_inventory_t.c.ordinal_position)
            ).mappings().all()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Helper: fetch tables that need extraction
    # ------------------------------------------------------------------

    def _fetch_pending_tables(self) -> list[dict[str, Any]]:
        """
        Return all non-excluded tables in scope for the active mode.

        ``full`` / ``sample`` — every row whose status is ``pending`` or
        ``extracted`` (already-extracted rows are skipped per-task by the
        run_log guard in full mode; sample re-extracts unconditionally).

        ``full_subset`` — additionally narrows to the supplied ``table_ids``
        whitelist.
        """
        stmt = select(
            tbl_inventory_t.c.table_id,
            tbl_inventory_t.c.schema_name,
            tbl_inventory_t.c.table_name,
        ).where(tbl_inventory_t.c.status.in_(["pending", "extracted"]))

        if self._mode == "full_subset" and self._table_ids is not None:
            stmt = stmt.where(tbl_inventory_t.c.table_id.in_(self._table_ids))

        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Public Phase 2 entry point
# ---------------------------------------------------------------------------


def run_phase_2(
    engine: Engine,
    extraction_client: ExtractionClient,
    config: Any,
    *,
    mode: ExtractionMode = "full",
    table_ids: Iterable[int] | None = None,
    sample_pct: float = _DEFAULT_SAMPLE_PCT,
    limit: int | None = None,
) -> None:
    """
    Extract every pending table in tbl_inventory to a local Parquet file via
    the extraction service.

    Idempotent: each per-table task is gated by run_log.is_complete (full
    mode only); the Parquet file is written at a deterministic path so
    re-extraction is safe.

    Parameters
    ----------
    engine:
        SQLAlchemy engine for the results DB.
    extraction_client:
        HTTP client for the Spring Boot extraction service.
    config:
        Loaded AppConfig.  Honours ``config.extraction.column_projection``
        when present (defaults to True); other knobs come from
        ``orchestration``, ``storage``, ``source_db`` as before.
    mode:
        ``'full'`` (default) — full extract per table; lifecycle owned here.
        ``'sample'`` — TABLESAMPLE BERNOULLI(``sample_pct``) triage extract;
        writes to ``<table>.sample.parquet`` and does NOT mutate
        tbl_inventory state.
        ``'full_subset'`` — full extract restricted to *table_ids* (used by
        the orchestrator for Pass-2 retake of survivor tables).
    table_ids:
        Optional iterable of ``tbl_inventory.table_id`` values.  Only honoured
        when ``mode='full_subset'``; ignored otherwise.
    sample_pct:
        Per-table TABLESAMPLE BERNOULLI percentage in the half-open range
        ``(0, 100]`` (e.g. ``1.0`` = 1%, ``5.0`` = 5%).  Default ``1.0``.
        Only consumed when ``mode='sample'``; silently ignored for ``'full'``
        and ``'full_subset'``.  Values outside ``(0, 100]`` raise
        ``ValueError``.

        Caveat: PII detection on sampled data may miss rare-token PII
        (long-tail rows where a single email lives in 100M ``notes`` rows
        is invisible at p=1%).  The two-pass orchestrator re-runs full
        extraction + Phase 5 only on the surviving candidate tables, but
        the Phase 3b PII findings from the sampled pass are NOT recomputed
        automatically — operators should re-run ``discovery pii-scan``
        after ``--two-pass`` if PII coverage matters.
    limit:
        Optional cap on the number of tables to process — useful for
        ``--limit`` debugging runs.

    NOTE: this function does NOT write to the global-scope run_log itself;
    the orchestrator owns global-phase lifecycle.  Per-table run_log writes
    (scope_type='table') still happen inside the runner for full mode.  When
    invoked directly via the CLI, the ``is_complete`` guard below short-
    circuits a re-run.
    """
    # Validate sample_pct early so a bad value fails before any DB / HTTP
    # work is done.  The runner re-validates at SQL-build time as
    # defence-in-depth (see _ExtractionRunner._build_query).
    pct = float(sample_pct)
    if not (0.0 < pct <= 100.0):
        raise ValueError(
            f"sample_pct must be in the half-open range (0, 100]; got {sample_pct!r}"
        )

    run_log = RunLog(engine)

    # The global short-circuit only applies to a normal full pass.  Sample
    # and full_subset are typically driven by the orchestrator at a stage
    # when the global 'extract' phase is already 'succeeded' — short-
    # circuiting them would defeat their purpose.
    if mode == "full" and run_log.is_complete("extract", "global", None):
        log.info("extraction.skip_complete")
        return

    src_cfg = config.source_db
    storage_cfg = config.storage
    svc_cfg = config.extraction_service
    workers = getattr(
        getattr(config.orchestration, "workers", None),
        "extract",
        _DEFAULT_WORKERS,
    )

    # Defensive config lookup — config.py doesn't yet model an
    # ``ExtractionConfig`` block, so we fall through to the sensible default
    # when the section is absent.  When config.py grows this knob the existing
    # callers continue to work unchanged.
    column_projection = bool(
        getattr(
            getattr(config, "extraction", None),
            "column_projection",
            True,
        )
    )

    runner = _ExtractionRunner(
        extraction_client=extraction_client,
        engine=engine,
        run_log=run_log,
        source_conn_config=src_cfg.to_connection_config(),
        storage_base_path=storage_cfg.base_path,
        request_timeout_seconds=svc_cfg.request_timeout_seconds,
        workers=workers,
        limit=limit,
        mode=mode,
        table_ids=table_ids,
        column_projection=column_projection,
        sample_pct=pct,
    )

    runner.run()
    log.info("extraction.phase_complete", mode=mode)
