"""
report.py — Phase 7: generate CSV and Excel reports from the results DB.

Each public function writes one or more files under *out_dir* and returns the
list of paths produced.

Design choices
--------------
* CSV is written via :mod:`csv` (stdlib) to avoid pulling pandas into the CSV
  path — keeps memory usage low for large result sets.
* Excel is written via :mod:`pandas` + :mod:`openpyxl` — pandas wraps
  ExcelWriter cleanly and openpyxl is already a project dependency.
* Queries use SQLAlchemy Core text() so they work without ORM mappings.
* ``generate_all`` calls all four functions and logs totals.
* The summary report is a Markdown file (``summary.md``) — plain text, easy
  to page in a terminal.

Security
--------
No raw PII leaves the database: ``pii_findings.redacted_examples`` is stored
already-redacted; the report includes it as-is.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import structlog
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_csv(filepath: Path, headers: list[str], rows: list[tuple[Any, ...]]) -> None:
    """Write *rows* as CSV to *filepath* using csv.writer."""
    with filepath.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)


def _write_excel(filepath: Path, sheet_name: str, df: pd.DataFrame) -> None:
    """Write *df* to an Excel file (xlsx) with a single sheet.

    Excel can't represent timezone-aware datetimes; strip tz on any such
    columns before writing (values are normalised to UTC first).
    """
    df = df.copy()
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s) and getattr(s.dt, "tz", None) is not None:
            df[col] = s.dt.tz_convert("UTC").dt.tz_localize(None)
    with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)


# ---------------------------------------------------------------------------
# report_relationships
# ---------------------------------------------------------------------------

_RELATIONSHIPS_QUERY = text(
    """
    SELECT
        child_tbl.schema_name           AS child_schema,
        child_tbl.table_name            AS child_table,
        child_col.column_name           AS child_col,
        parent_tbl.schema_name          AS parent_schema,
        parent_tbl.table_name           AS parent_table,
        parent_col.column_name          AS parent_col,
        r.cardinality,
        r.containment_full,
        r.confidence,
        r.discovered_at
    FROM discovery.relationships r
    JOIN discovery.col_inventory  child_col
        ON r.child_col_id  = child_col.column_id
    JOIN discovery.tbl_inventory  child_tbl
        ON child_col.table_id = child_tbl.table_id
    JOIN discovery.col_inventory  parent_col
        ON r.parent_col_id = parent_col.column_id
    JOIN discovery.tbl_inventory  parent_tbl
        ON parent_col.table_id = parent_tbl.table_id
    ORDER BY child_schema, child_table, child_col
    """
)

_RELATIONSHIPS_HEADERS = [
    "child_schema",
    "child_table",
    "child_col",
    "parent_schema",
    "parent_table",
    "parent_col",
    "cardinality",
    "containment_full",
    "confidence",
    "discovered_at",
]


def report_relationships(engine: Engine, out_dir: Path) -> list[Path]:
    """
    Write ``relationships.csv`` and ``relationships.xlsx`` under *out_dir*.

    Returns
    -------
    list[Path]
        Paths of the files written.
    """
    _ensure_dir(out_dir)

    with engine.connect() as conn:
        result = conn.execute(_RELATIONSHIPS_QUERY)
        rows = result.fetchall()

    log.info("report_relationships_fetched", row_count=len(rows))

    tuples: list[tuple[Any, ...]] = [tuple(r) for r in rows]

    csv_path = out_dir / "relationships.csv"
    _write_csv(csv_path, _RELATIONSHIPS_HEADERS, tuples)

    df = pd.DataFrame(tuples, columns=_RELATIONSHIPS_HEADERS)
    xlsx_path = out_dir / "relationships.xlsx"
    _write_excel(xlsx_path, "relationships", df)

    log.info(
        "report_relationships_written",
        csv=str(csv_path),
        xlsx=str(xlsx_path),
        rows=len(rows),
    )
    return [csv_path, xlsx_path]


# ---------------------------------------------------------------------------
# report_relationships_advisory
# ---------------------------------------------------------------------------
#
# Advisory low-confidence FK candidates surfaced by Phase 4 but skipped by
# Phase 5 validation.  These are the rows persisted with
# ``fk_candidates.tier='advisory_lowconf'``.  We report them separately so a
# reviewer can audit precision/recall trade-offs without polluting the main
# relationships report.

_ADVISORY_QUERY = text(
    """
    SELECT
        child_tbl.schema_name           AS child_schema,
        child_tbl.table_name            AS child_table,
        child_col.column_name           AS child_col,
        parent_tbl.schema_name          AS parent_schema,
        parent_tbl.table_name           AS parent_table,
        parent_col.column_name          AS parent_col,
        fc.estimated_containment,
        fc.name_similarity,
        fc.source_stage,
        fc.tier,
        fc.created_at
    FROM discovery.fk_candidates fc
    JOIN discovery.col_inventory child_col
        ON fc.child_col_id = child_col.column_id
    JOIN discovery.tbl_inventory child_tbl
        ON child_col.table_id = child_tbl.table_id
    JOIN discovery.col_inventory parent_col
        ON fc.parent_col_id = parent_col.column_id
    JOIN discovery.tbl_inventory parent_tbl
        ON parent_col.table_id = parent_tbl.table_id
    WHERE fc.tier = 'advisory_lowconf'
    ORDER BY child_schema, child_table, child_col,
             fc.estimated_containment DESC NULLS LAST
    """
)

_ADVISORY_HEADERS = [
    "child_schema",
    "child_table",
    "child_col",
    "parent_schema",
    "parent_table",
    "parent_col",
    "estimated_containment",
    "name_similarity",
    "source_stage",
    "tier",
    "created_at",
]


def report_relationships_advisory(engine: Engine, out_dir: Path) -> list[Path]:
    """
    Write ``relationships_advisory.csv`` under *out_dir*.

    Lists FK candidates that survived type / cardinality gating but lacked
    strong evidence (parent_pk_unknown, both-side-PK, dense-serial pair, or
    weak name & containment evidence) and were therefore tagged
    ``advisory_lowconf`` by Phase 4. Phase 5 does not validate them, so they
    never reach :class:`relationships`. Useful for auditing recall trade-offs.

    Returns
    -------
    list[Path]
        Path(s) of the file(s) written.  An empty advisory set still
        produces a header-only CSV — keeps reporting deterministic.
    """
    _ensure_dir(out_dir)

    with engine.connect() as conn:
        result = conn.execute(_ADVISORY_QUERY)
        rows = result.fetchall()

    log.info("report_relationships_advisory_fetched", row_count=len(rows))

    tuples: list[tuple[Any, ...]] = [tuple(r) for r in rows]

    csv_path = out_dir / "relationships_advisory.csv"
    _write_csv(csv_path, _ADVISORY_HEADERS, tuples)

    log.info(
        "report_relationships_advisory_written",
        csv=str(csv_path),
        rows=len(rows),
    )
    return [csv_path]


# ---------------------------------------------------------------------------
# report_pii
# ---------------------------------------------------------------------------

_PII_QUERY = text(
    """
    SELECT
        tbl.schema_name         AS schema,
        tbl.table_name          AS "table",
        col.column_name         AS "column",
        pf.pii_type,
        pf.detector,
        pf.match_count,
        pf.match_rate,
        pf.validated,
        pf.redacted_examples
    FROM discovery.pii_findings pf
    JOIN discovery.col_inventory col
        ON pf.column_id = col.column_id
    JOIN discovery.tbl_inventory tbl
        ON col.table_id = tbl.table_id
    ORDER BY tbl.schema_name, tbl.table_name, col.column_name, pf.pii_type
    """
)

_PII_HEADERS = [
    "schema",
    "table",
    "column",
    "pii_type",
    "detector",
    "match_count",
    "match_rate",
    "validated",
    "redacted_examples",
]


def report_pii(engine: Engine, out_dir: Path) -> list[Path]:
    """
    Write ``pii_findings.csv`` and ``pii_findings.xlsx`` under *out_dir*.

    ``redacted_examples`` is stored as JSONB in the DB; it is serialised to
    its JSON string representation in the CSV.

    Returns
    -------
    list[Path]
        Paths of the files written.
    """
    _ensure_dir(out_dir)

    with engine.connect() as conn:
        result = conn.execute(_PII_QUERY)
        rows = result.fetchall()

    log.info("report_pii_fetched", row_count=len(rows))

    # Normalise redacted_examples: psycopg2 returns JSONB as dict; serialise
    # back to a JSON string for the flat CSV/Excel cells.
    normalised: list[tuple[Any, ...]] = []
    for row in rows:
        row_list = list(row)
        if row_list[-1] is not None and not isinstance(row_list[-1], str):
            row_list[-1] = json.dumps(row_list[-1])
        normalised.append(tuple(row_list))

    csv_path = out_dir / "pii_findings.csv"
    _write_csv(csv_path, _PII_HEADERS, normalised)

    df = pd.DataFrame(normalised, columns=_PII_HEADERS)
    xlsx_path = out_dir / "pii_findings.xlsx"
    _write_excel(xlsx_path, "pii_findings", df)

    log.info(
        "report_pii_written",
        csv=str(csv_path),
        xlsx=str(xlsx_path),
        rows=len(rows),
    )
    return [csv_path, xlsx_path]


# ---------------------------------------------------------------------------
# report_exclusions
# ---------------------------------------------------------------------------

_EXCLUSIONS_QUERY = text(
    """
    SELECT
        schema_name,
        table_name,
        exclusion_reason,
        row_count_estimate,
        byte_size_estimate
    FROM discovery.tbl_inventory
    WHERE status = 'excluded'
    ORDER BY schema_name, table_name
    """
)

_EXCLUSIONS_HEADERS = [
    "schema",
    "table",
    "exclusion_reason",
    "row_count_estimate",
    "byte_size_estimate",
]


def report_exclusions(engine: Engine, out_dir: Path) -> list[Path]:
    """
    Write ``exclusions.csv`` and ``exclusions.xlsx`` under *out_dir*.

    Returns
    -------
    list[Path]
        Paths of the files written.
    """
    _ensure_dir(out_dir)

    with engine.connect() as conn:
        result = conn.execute(_EXCLUSIONS_QUERY)
        rows = result.fetchall()

    log.info("report_exclusions_fetched", row_count=len(rows))

    tuples = [tuple(r) for r in rows]

    csv_path = out_dir / "exclusions.csv"
    _write_csv(csv_path, _EXCLUSIONS_HEADERS, tuples)

    df = pd.DataFrame(tuples, columns=_EXCLUSIONS_HEADERS)
    xlsx_path = out_dir / "exclusions.xlsx"
    _write_excel(xlsx_path, "exclusions", df)

    log.info(
        "report_exclusions_written",
        csv=str(csv_path),
        xlsx=str(xlsx_path),
        rows=len(rows),
    )
    return [csv_path, xlsx_path]


# ---------------------------------------------------------------------------
# report_summary
# ---------------------------------------------------------------------------

_SUMMARY_COUNTS_QUERY = text(
    """
    SELECT phase, status, count(*) AS cnt
    FROM discovery.run_log
    GROUP BY phase, status
    ORDER BY phase, status
    """
)

_TABLE_STATUS_QUERY = text(
    """
    SELECT status, count(*) AS cnt
    FROM discovery.tbl_inventory
    GROUP BY status
    ORDER BY status
    """
)

_RELATIONSHIP_COUNT_QUERY = text(
    "SELECT count(*) FROM discovery.relationships"
)

_PII_COUNT_QUERY = text(
    "SELECT count(*) FROM discovery.pii_findings"
)


def report_summary(engine: Engine, out_dir: Path) -> list[Path]:
    """
    Write ``summary.md`` under *out_dir* with high-level counts per phase.

    Returns
    -------
    list[Path]
        Paths of the files written.
    """
    _ensure_dir(out_dir)

    with engine.connect() as conn:
        phase_rows = conn.execute(_SUMMARY_COUNTS_QUERY).fetchall()
        tbl_rows = conn.execute(_TABLE_STATUS_QUERY).fetchall()
        rel_count = conn.execute(_RELATIONSHIP_COUNT_QUERY).scalar() or 0
        pii_count = conn.execute(_PII_COUNT_QUERY).scalar() or 0

    now_utc = datetime.now(tz=timezone.utc).isoformat()

    # Build table-status map
    tbl_status: dict[str, int] = {str(r[0]): int(r[1]) for r in tbl_rows}

    # Build phase-status map: phase -> {status -> count}
    phase_status: dict[str, dict[str, int]] = {}
    for row in phase_rows:
        phase, status, cnt = str(row[0]), str(row[1]), int(row[2])
        phase_status.setdefault(phase, {})[status] = cnt

    lines: list[str] = [
        "# Discovery Pipeline — Run Summary",
        "",
        f"Generated: {now_utc}",
        "",
        "## Table inventory",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status_val, cnt in sorted(tbl_status.items()):
        lines.append(f"| {status_val} | {cnt} |")

    lines += [
        "",
        "## Phase run-log",
        "",
        "| Phase | Status | Count |",
        "|-------|--------|-------|",
    ]
    for phase in sorted(phase_status):
        for status_val, cnt in sorted(phase_status[phase].items()):
            lines.append(f"| {phase} | {status_val} | {cnt} |")

    lines += [
        "",
        "## Findings",
        "",
        f"- Relationships discovered: **{rel_count}**",
        f"- PII findings: **{pii_count}**",
        "",
    ]

    md_content = "\n".join(lines)
    md_path = out_dir / "summary.md"
    md_path.write_text(md_content, encoding="utf-8")

    log.info(
        "report_summary_written",
        path=str(md_path),
        relationships=rel_count,
        pii_findings=pii_count,
    )
    return [md_path]


# ---------------------------------------------------------------------------
# generate_all
# ---------------------------------------------------------------------------


def generate_all(engine: Engine, config: Any) -> list[Path]:
    """
    Run all four reports and return the full list of paths produced.

    Parameters
    ----------
    engine:
        SQLAlchemy engine connected to the results DB.
    config:
        Pipeline config object.  ``config.reporting.output_dir`` is used as
        the output directory.
    """
    out_dir = Path(config.reporting.output_dir)

    log.info("report_generate_all_start", out_dir=str(out_dir))

    all_paths: list[Path] = []
    all_paths.extend(report_relationships(engine, out_dir))
    # Best-effort: advisory relationships rely on the fk_candidates.tier column
    # introduced by the FK-precision pass.  Older databases without the
    # migration applied raise ProgrammingError ("column tier does not exist");
    # log and continue rather than failing the whole report.
    try:
        all_paths.extend(report_relationships_advisory(engine, out_dir))
    except Exception as exc:
        log.warning(
            "report_relationships_advisory_skipped",
            error=str(exc),
        )
    all_paths.extend(report_pii(engine, out_dir))
    all_paths.extend(report_exclusions(engine, out_dir))
    all_paths.extend(report_summary(engine, out_dir))

    log.info(
        "report_generate_all_complete",
        files_written=len(all_paths),
        out_dir=str(out_dir),
    )
    return all_paths
