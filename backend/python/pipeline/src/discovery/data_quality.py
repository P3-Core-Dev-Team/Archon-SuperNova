"""
Data-quality phase — null density / duplicate-PK / format-consistency
profiling on already-extracted Parquet files.

This phase fills the gap surfaced by the architect-review pass: Archon's
existing pipeline produces an FK graph and a PII inventory but doesn't
profile the underlying *quality* of the data.  An analyst dropping fresh
data on the platform deserves to know up-front which columns are 80%
NULL, which putative PKs aren't actually unique, and which string
columns mix `'USA'` with `'usa'`.

Architecture
------------
* Pure DuckDB-on-Parquet, same engine + per-worker connection pattern
  as Phase 5 (validate.py).  No source-DB connection needed.
* One task per table.  Each worker computes per-column metrics in a
  single SQL query so the parquet footer + column dictionary load is
  amortised.
* Findings persist into ``data_quality_findings`` keyed on
  ``(column_id, issue_type)``; re-running the phase is idempotent.

Issue catalogue
---------------
``IssueType``                        Severity   Detector
NULL_HEAVY                           MEDIUM     null fraction > 0.50 (configurable)
ALL_NULL                             HIGH       null fraction == 1.0
DUPLICATE_PK                         HIGH       declared/inferred PK has duplicate values
LEADING_TRAILING_WHITESPACE          LOW        col_value != trim(col_value)
EMPTY_STRING                         LOW        '' counted alongside NULL — separate signal
MIXED_CASE                           MEDIUM     COUNT(DISTINCT lower(v)) < COUNT(DISTINCT v)
LOW_CARDINALITY                      LOW        distinct_count < 5 on a sample of 1000+
                                                rows (informational; helps DIM detection)

Configuration
-------------
``DataQualityConfig`` (added to AppConfig.relationships for now to avoid
yet another top-level config block):

  data_quality_enabled              : bool = True
  data_quality_null_threshold       : float = 0.50
  data_quality_max_rows_per_table   : int = 100_000
  data_quality_max_examples         : int = 3
"""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class IssueType:
    """Stable string constants for the data-quality issue catalogue.

    Strings live in the DB; renaming requires a migration.  Add new
    values at the bottom; never remove or rename.
    """

    NULL_HEAVY = "NULL_HEAVY"
    ALL_NULL = "ALL_NULL"
    DUPLICATE_PK = "DUPLICATE_PK"
    LEADING_TRAILING_WHITESPACE = "LEADING_TRAILING_WHITESPACE"
    EMPTY_STRING = "EMPTY_STRING"
    MIXED_CASE = "MIXED_CASE"
    LOW_CARDINALITY = "LOW_CARDINALITY"


_SEVERITY_BY_TYPE: dict[str, str] = {
    IssueType.ALL_NULL: "HIGH",
    IssueType.DUPLICATE_PK: "HIGH",
    IssueType.NULL_HEAVY: "MEDIUM",
    IssueType.MIXED_CASE: "MEDIUM",
    IssueType.LEADING_TRAILING_WHITESPACE: "LOW",
    IssueType.EMPTY_STRING: "LOW",
    IssueType.LOW_CARDINALITY: "LOW",
}


# ---------------------------------------------------------------------------
# Pure helper: classify a column's metrics into a list of findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnMetrics:
    """One pass of per-column profiling output."""

    column_id: int
    column_name: str
    sample_rows: int
    null_count: int
    distinct_count: int
    is_pk: bool
    # True when this column is the table's SOLE primary-key column.
    # Composite-PK members have ``is_pk=True`` but ``is_sole_pk=False``;
    # the DUPLICATE_PK detector skips them because uniqueness is on
    # the (composite-key) tuple, not per-column.
    is_sole_pk: bool = False
    # Optional string-only metrics — None when the column isn't a string.
    whitespace_count: Optional[int] = None
    empty_count: Optional[int] = None
    distinct_lower_count: Optional[int] = None
    samples_whitespace: Optional[list[str]] = None
    samples_mixed_case: Optional[list[str]] = None


def classify_metrics(
    m: ColumnMetrics,
    *,
    null_threshold: float = 0.50,
    low_card_floor: int = 5,
    low_card_min_rows: int = 1000,
) -> list[dict[str, Any]]:
    """Convert one ColumnMetrics into zero-or-more findings.

    Pure: no side effects, no DB / file IO.  Easy to unit-test by
    constructing ColumnMetrics directly.  Each returned dict is
    ready to upsert into ``data_quality_findings``.
    """
    out: list[dict[str, Any]] = []
    rows = max(1, m.sample_rows)

    # Null density --------------------------------------------------
    null_frac = m.null_count / rows
    if m.null_count == m.sample_rows and m.sample_rows > 0:
        out.append({
            "column_id": m.column_id,
            "issue_type": IssueType.ALL_NULL,
            "severity": _SEVERITY_BY_TYPE[IssueType.ALL_NULL],
            "count": m.null_count,
            "sample_rows": m.sample_rows,
            "fraction": 1.0,
            "samples": [],
        })
    elif null_frac >= null_threshold and m.null_count > 0:
        out.append({
            "column_id": m.column_id,
            "issue_type": IssueType.NULL_HEAVY,
            "severity": _SEVERITY_BY_TYPE[IssueType.NULL_HEAVY],
            "count": m.null_count,
            "sample_rows": m.sample_rows,
            "fraction": round(null_frac, 4),
            "samples": [],
        })

    # Duplicate PK --------------------------------------------------
    # Only fires for SINGLE-column PKs — composite-PK members
    # legitimately have duplicates per-column (uniqueness is on the
    # tuple, not the column).  Without this gate every junction table
    # generated 2+ false-positive DUPLICATE_PK findings.
    non_null = m.sample_rows - m.null_count
    if (
        m.is_pk
        and m.is_sole_pk
        and non_null > 0
        and m.distinct_count < non_null
    ):
        dup_count = non_null - m.distinct_count
        out.append({
            "column_id": m.column_id,
            "issue_type": IssueType.DUPLICATE_PK,
            "severity": _SEVERITY_BY_TYPE[IssueType.DUPLICATE_PK],
            "count": dup_count,
            "sample_rows": m.sample_rows,
            "fraction": round(dup_count / non_null, 4),
            "samples": [],
        })

    # Whitespace ----------------------------------------------------
    if m.whitespace_count is not None and m.whitespace_count > 0:
        out.append({
            "column_id": m.column_id,
            "issue_type": IssueType.LEADING_TRAILING_WHITESPACE,
            "severity": _SEVERITY_BY_TYPE[IssueType.LEADING_TRAILING_WHITESPACE],
            "count": m.whitespace_count,
            "sample_rows": m.sample_rows,
            "fraction": round(m.whitespace_count / rows, 4),
            "samples": list(m.samples_whitespace or [])[:3],
        })

    # Empty string --------------------------------------------------
    if m.empty_count is not None and m.empty_count > 0:
        out.append({
            "column_id": m.column_id,
            "issue_type": IssueType.EMPTY_STRING,
            "severity": _SEVERITY_BY_TYPE[IssueType.EMPTY_STRING],
            "count": m.empty_count,
            "sample_rows": m.sample_rows,
            "fraction": round(m.empty_count / rows, 4),
            "samples": [],
        })

    # Mixed case ----------------------------------------------------
    if (
        m.distinct_lower_count is not None
        and m.distinct_count > 0
        and m.distinct_lower_count < m.distinct_count
    ):
        collision = m.distinct_count - m.distinct_lower_count
        out.append({
            "column_id": m.column_id,
            "issue_type": IssueType.MIXED_CASE,
            "severity": _SEVERITY_BY_TYPE[IssueType.MIXED_CASE],
            "count": collision,
            "sample_rows": m.sample_rows,
            "fraction": round(collision / max(m.distinct_count, 1), 4),
            "samples": list(m.samples_mixed_case or [])[:3],
        })

    # Low cardinality ----------------------------------------------
    # Skip PK columns — they're expected to be unique.  Skip tiny
    # samples — distinct_count==2 on 50 rows isn't informative.
    if (
        not m.is_pk
        and m.sample_rows >= low_card_min_rows
        and 0 < m.distinct_count < low_card_floor
    ):
        out.append({
            "column_id": m.column_id,
            "issue_type": IssueType.LOW_CARDINALITY,
            "severity": _SEVERITY_BY_TYPE[IssueType.LOW_CARDINALITY],
            "count": m.distinct_count,
            "sample_rows": m.sample_rows,
            "fraction": round(m.distinct_count / m.sample_rows, 4),
            "samples": [],
        })

    return out


# ---------------------------------------------------------------------------
# Per-worker DuckDB connection (mirrors validate.py pattern)
# ---------------------------------------------------------------------------


_worker_con: Any = None


def _worker_init(settings: dict) -> None:
    """Open a DuckDB connection once per worker process."""
    global _worker_con
    import duckdb  # noqa: PLC0415

    _worker_con = duckdb.connect()
    _worker_con.execute(
        f"SET memory_limit = '{settings.get('memory_limit_per_worker', '1GB')}'"
    )
    if settings.get("temp_directory"):
        _worker_con.execute(
            f"SET temp_directory = '{settings['temp_directory']}'"
        )
    _worker_con.execute("SET enable_object_cache=true")


def _profile_table_task(
    arg: tuple,
) -> list[dict[str, Any]]:
    """Worker entry point: profile every column of one parquet file.

    Input::

        (
            parquet_path,
            [
                (column_id, column_name, data_type, is_pk, is_sole_pk),
                ...
            ],
            max_rows,
            max_examples,
        )

    Returns a flat list of finding dicts (one per detected issue,
    across all columns of this table).  Failures inside the worker
    are caught and the column is skipped; one bad column doesn't kill
    the whole table.
    """
    if _worker_con is None:
        return []
    parquet_path, col_specs, max_rows, max_examples = arg

    # LIMIT the read so a 100M-row table doesn't consume the worker's
    # memory budget.  Real value distribution is preserved for null /
    # duplicate / format checks in the first N rows.
    base_select = (
        f"SELECT * FROM read_parquet('{parquet_path}') "
        f"LIMIT {int(max_rows)}"
    )
    findings: list[dict[str, Any]] = []
    try:
        # Materialise the limited view once per table — every per-column
        # query reads from this in-memory CTE so the parquet is scanned
        # once.  DuckDB CTEs are NOT materialised by default; using
        # ``CREATE TEMP TABLE`` forces it.
        _worker_con.execute(
            f"CREATE OR REPLACE TEMP TABLE _dq_sample AS {base_select}"
        )
        sample_rows = int(
            _worker_con.execute("SELECT COUNT(*) FROM _dq_sample").fetchone()[0]
        )
        if sample_rows == 0:
            return []
    except Exception as exc:  # pragma: no cover  — defensive
        log.warning(
            "data_quality_table_skipped",
            parquet_path=parquet_path,
            error=str(exc),
        )
        return []

    for column_id, column_name, data_type, is_pk, is_sole_pk in col_specs:
        try:
            metrics = _profile_column(
                column_name, data_type, is_pk, sample_rows,
                max_examples=int(max_examples),
                column_id=int(column_id),
                is_sole_pk=bool(is_sole_pk),
            )
        except Exception as exc:
            log.warning(
                "data_quality_column_skipped",
                parquet_path=parquet_path,
                column=column_name,
                error=str(exc),
            )
            continue
        if metrics is None:
            continue
        findings.extend(classify_metrics(metrics))

    return findings


def _profile_column(
    column_name: str,
    data_type: str,
    is_pk: bool,
    sample_rows: int,
    *,
    max_examples: int,
    column_id: int,
    is_sole_pk: bool = False,
) -> Optional[ColumnMetrics]:
    """Run the per-column SQL probe against the cached _dq_sample.

    Returns ``ColumnMetrics`` or ``None`` if the column isn't queryable
    (parquet writer dropped it, etc.).
    """
    if _worker_con is None:
        return None
    quoted = f'"{column_name}"'
    is_text = _looks_like_text(data_type)

    # One query per column — kept compact so DuckDB can plan it
    # efficiently.  Text-only branches use CASE so non-text columns
    # don't blow up on `trim()` of non-strings.
    if is_text:
        sql = (
            f"SELECT "
            f"  SUM(CASE WHEN {quoted} IS NULL THEN 1 ELSE 0 END), "
            f"  COUNT(DISTINCT {quoted}), "
            f"  SUM(CASE WHEN {quoted} IS NOT NULL "
            f"           AND {quoted} <> trim({quoted}) THEN 1 ELSE 0 END), "
            f"  SUM(CASE WHEN {quoted} = '' THEN 1 ELSE 0 END), "
            f"  COUNT(DISTINCT lower({quoted})) "
            f"FROM _dq_sample"
        )
        nulls, distinct, ws, empty, distinct_lower = _worker_con.execute(
            sql
        ).fetchone()
        # Sample values for the chip tooltips.
        ws_samples: list[str] = []
        mc_samples: list[str] = []
        if ws and ws > 0:
            ws_samples = [
                _redact(str(r[0]))
                for r in _worker_con.execute(
                    f"SELECT {quoted} FROM _dq_sample "
                    f"WHERE {quoted} IS NOT NULL "
                    f"  AND {quoted} <> trim({quoted}) LIMIT {max_examples}"
                ).fetchall()
            ]
        if (
            distinct is not None
            and distinct_lower is not None
            and distinct_lower < distinct
        ):
            # Pick a few representative collision-y values.
            mc_samples = [
                _redact(str(r[0]))
                for r in _worker_con.execute(
                    f"SELECT {quoted} FROM _dq_sample "
                    f"WHERE {quoted} IS NOT NULL "
                    f"GROUP BY 1 LIMIT {max_examples}"
                ).fetchall()
            ]
        return ColumnMetrics(
            column_id=column_id,
            column_name=column_name,
            sample_rows=sample_rows,
            null_count=int(nulls or 0),
            distinct_count=int(distinct or 0),
            is_pk=bool(is_pk),
            is_sole_pk=bool(is_sole_pk),
            whitespace_count=int(ws or 0),
            empty_count=int(empty or 0),
            distinct_lower_count=int(distinct_lower or 0),
            samples_whitespace=ws_samples,
            samples_mixed_case=mc_samples,
        )
    # Non-text path — null / distinct only.
    nulls, distinct = _worker_con.execute(
        f"SELECT "
        f"  SUM(CASE WHEN {quoted} IS NULL THEN 1 ELSE 0 END), "
        f"  COUNT(DISTINCT {quoted}) "
        f"FROM _dq_sample"
    ).fetchone()
    return ColumnMetrics(
        column_id=column_id,
        column_name=column_name,
        sample_rows=sample_rows,
        null_count=int(nulls or 0),
        distinct_count=int(distinct or 0),
        is_pk=bool(is_pk),
        is_sole_pk=bool(is_sole_pk),
    )


def _looks_like_text(data_type: str) -> bool:
    if not data_type:
        return False
    t = data_type.upper()
    return any(s in t for s in ("CHAR", "TEXT", "STRING", "VARCHAR", "CLOB", "JSON"))


def _redact(s: str) -> str:
    """Truncate + visible-whitespace-mark a sample so the UI can render
    it without pii leakage.  Whitespace samples need the literal
    bracketing to be useful in the tooltip."""
    if s is None:
        return ""
    s = s if len(s) <= 32 else s[:29] + "…"
    if s != s.strip():
        return f"[ws]{s!r}"
    return s


# ---------------------------------------------------------------------------
# Phase entry point
# ---------------------------------------------------------------------------


def run_phase_data_quality(engine: Any, config: Any) -> dict[str, int]:
    """Run the data-quality phase.

    Returns ``{tables_scanned, columns_scanned, findings_written}``.
    """
    rel_cfg = getattr(config, "relationships", None)
    if not bool(getattr(rel_cfg, "data_quality_enabled", True)):
        log.info("data_quality_disabled")
        return {"tables_scanned": 0, "columns_scanned": 0, "findings_written": 0}

    null_threshold = float(getattr(rel_cfg, "data_quality_null_threshold", 0.50))
    max_rows = int(getattr(rel_cfg, "data_quality_max_rows_per_table", 100_000))
    max_examples = int(getattr(rel_cfg, "data_quality_max_examples", 3))

    orch_cfg = getattr(config, "orchestration", None)
    workers_cfg = getattr(orch_cfg, "workers", None)
    num_workers = int(getattr(workers_cfg, "validate_workers", 8))

    storage_cfg = getattr(config, "storage", None)
    duckdb_temp = getattr(storage_cfg, "duckdb_temp_dir", None)
    duckdb_mem = getattr(storage_cfg, "duckdb_memory_limit", "32GB")

    from sqlalchemy import select  # noqa: PLC0415

    from discovery.results_db import (  # noqa: PLC0415
        col_inventory_t,
        data_quality_findings_t,
        tbl_inventory_t,
        txn,
    )

    # Build per-table task list ------------------------------------
    with engine.connect() as conn:
        stmt = (
            select(
                tbl_inventory_t.c.table_id,
                tbl_inventory_t.c.parquet_path,
                col_inventory_t.c.column_id,
                col_inventory_t.c.column_name,
                col_inventory_t.c.data_type,
                col_inventory_t.c.is_pk,
            )
            .select_from(
                tbl_inventory_t.join(
                    col_inventory_t,
                    col_inventory_t.c.table_id == tbl_inventory_t.c.table_id,
                )
            )
            .where(tbl_inventory_t.c.parquet_path.isnot(None))
        )
        rows = conn.execute(stmt).all()

    if not rows:
        log.info("data_quality_no_tables_to_profile")
        return {"tables_scanned": 0, "columns_scanned": 0, "findings_written": 0}

    by_table: dict[tuple[int, str], list[tuple]] = {}
    for tid, ppath, cid, cname, dtype, is_pk in rows:
        by_table.setdefault((int(tid), str(ppath)), []).append(
            (int(cid), str(cname), str(dtype or ""), bool(is_pk))
        )

    # Compute per-table PK count so the worker can flag a column as
    # SOLE PK vs part-of-composite PK.  DUPLICATE_PK only makes sense
    # for sole PKs.
    pk_count_by_table: dict[int, int] = {
        tid: sum(1 for _, _, _, is_pk in cols if is_pk)
        for (tid, _ppath), cols in by_table.items()
    }
    tasks = [
        (
            ppath,
            [
                (cid, cname, dtype, is_pk, pk_count_by_table[tid] == 1)
                for (cid, cname, dtype, is_pk) in col_specs
            ],
            max_rows,
            max_examples,
        )
        for (tid, ppath), col_specs in by_table.items()
    ]

    log.info(
        "data_quality_pool_start",
        tables=len(tasks),
        workers=num_workers,
        null_threshold=null_threshold,
        max_rows=max_rows,
    )

    settings = {
        "memory_limit_per_worker": _per_worker_mem(duckdb_mem, num_workers),
        "temp_directory": duckdb_temp,
    }

    # Run pool ----------------------------------------------------
    findings: list[dict[str, Any]] = []
    with multiprocessing.Pool(
        processes=num_workers,
        initializer=_worker_init,
        initargs=(settings,),
    ) as pool:
        for batch_findings in pool.imap_unordered(_profile_table_task, tasks):
            findings.extend(batch_findings)

    # Filter by null_threshold (workers run with a default 0.50; honour
    # the configured value here for consistency if it differs).
    if null_threshold != 0.50:
        findings = [
            f for f in findings
            if f["issue_type"] != IssueType.NULL_HEAVY
            or f["fraction"] >= null_threshold
        ]

    # Persist -----------------------------------------------------
    # Clear-then-insert: a stale finding from a prior run (e.g. a
    # DUPLICATE_PK that's been fixed since) would otherwise survive
    # the upsert and confuse the user.  Scope the DELETE to the
    # column_ids we just profiled so we don't nuke findings on tables
    # outside this schema.
    from sqlalchemy import delete  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    column_ids = [
        int(cid) for col_specs in by_table.values() for cid, *_ in col_specs
    ]
    written = 0
    with txn(engine) as conn:
        if column_ids:
            conn.execute(
                delete(data_quality_findings_t).where(
                    data_quality_findings_t.c.column_id.in_(column_ids)
                )
            )
        for f in findings:
            stmt = insert(data_quality_findings_t).values(
                column_id=int(f["column_id"]),
                issue_type=str(f["issue_type"]),
                severity=str(f["severity"]),
                count=int(f["count"]),
                sample_rows=int(f["sample_rows"]),
                fraction=float(f["fraction"]),
                samples=f.get("samples") or [],
                detected_at=now,
            )
            # Belt-and-braces: should never conflict after the DELETE
            # above, but keep the upsert in case a parallel pipeline
            # writes between our DELETE and INSERT.
            stmt = stmt.on_conflict_do_update(
                index_elements=["column_id", "issue_type"],
                set_={
                    "severity": stmt.excluded.severity,
                    "count": stmt.excluded["count"],
                    "sample_rows": stmt.excluded.sample_rows,
                    "fraction": stmt.excluded.fraction,
                    "samples": stmt.excluded.samples,
                    "detected_at": stmt.excluded.detected_at,
                },
            )
            conn.execute(stmt)
            written += 1

    log.info(
        "data_quality_done",
        tables_scanned=len(tasks),
        columns_scanned=sum(len(s) for s in by_table.values()),
        findings_written=written,
    )
    return {
        "tables_scanned": len(tasks),
        "columns_scanned": sum(len(s) for s in by_table.values()),
        "findings_written": written,
    }


def _per_worker_mem(total: str, workers: int) -> str:
    """Best-effort split of a `'32GB'` total across worker processes."""
    if workers <= 0:
        return total
    if isinstance(total, str) and total.upper().endswith("GB"):
        try:
            n = float(total[:-2]) / max(workers, 1)
            return f"{n:.1f}GB"
        except ValueError:
            pass
    return total
