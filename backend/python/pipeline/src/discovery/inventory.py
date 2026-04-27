"""
inventory.py — Phase 1: Inventory.

Enumerates all user tables in the configured source schemas via the extraction
service (NOT direct DB connections), then writes the results to tbl_inventory
and col_inventory in the results DB.

Flow per schema:
  1. Extract information_schema.tables via extraction service → Parquet
  2. Extract information_schema.columns via extraction service → Parquet
  3. Extract pg_catalog.pg_stats via extraction service → Parquet (best-effort)
  4. Extract information_schema.table_constraints via extraction service → Parquet
  5. Extract information_schema.key_column_usage via extraction service → Parquet
  6. Read Parquet files with DuckDB locally
  7. Apply exclusion filters
  8. Upsert tbl_inventory + col_inventory via results_db DAOs
  9. Record progress in run_log

All operations are idempotent (upserts).  The phase records a single global
run_log row for the whole inventory pass; per-schema iteration relies on the
upsert idempotency for resumability.

Important: every query sent to the extraction service MUST be a single-table
SELECT with a single WHERE clause (no JOIN, GROUP BY, DISTINCT, subquery,
CTE, or aggregate).  The Spring Boot whitelist enforces this.

Security: never opens a direct connection to the source DB.  All source data
is retrieved via extraction_client.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import structlog

from discovery.exclusions import should_exclude
from discovery.extraction_client import ExtractionClient
from discovery.models import ExtractionOptions, ExtractionRequest, OutputConfig
from discovery.results_db import ColInventory, TblInventory, txn
from discovery.run_log import RunLog
from discovery.type_class import TypeClass, classify_pg_type, is_fk_eligible

log = structlog.get_logger("discovery.inventory")

# ---------------------------------------------------------------------------
# SQL queries sent to the extraction service.
#
# IMPORTANT: every query is a SINGLE-TABLE SELECT with a single WHERE.  The
# Spring Boot QueryWhitelistValidator rejects JOIN, GROUP BY, DISTINCT,
# subquery, CTE, aggregate functions.  Any join is performed in Python after
# Parquet readback (via DuckDB).
# ---------------------------------------------------------------------------

_TABLES_QUERY = (
    "SELECT * FROM information_schema.tables "
    "WHERE table_schema = '{schema}'"
)

_COLUMNS_QUERY = (
    "SELECT * FROM information_schema.columns "
    "WHERE table_schema = '{schema}'"
)

# pg_stats: best-effort.  most_common_vals is anyarray (Parquet can't store it);
# we project explicit columns to drop it, but per the whitelist we must use a
# wildcard *or* a flat column list — using the column list is whitelisted.
_STATS_QUERY = (
    "SELECT schemaname, tablename, attname, null_frac, n_distinct, avg_width "
    "FROM pg_catalog.pg_stats "
    "WHERE schemaname = '{schema}'"
)

# Primary keys: two single-table queries, joined in Python.
_TABLE_CONSTRAINTS_QUERY = (
    "SELECT * FROM information_schema.table_constraints "
    "WHERE table_schema = '{schema}'"
)

_KEY_COLUMN_USAGE_QUERY = (
    "SELECT * FROM information_schema.key_column_usage "
    "WHERE table_schema = '{schema}'"
)

# ---------------------------------------------------------------------------
# Index discovery: pg_catalog tables, each pulled in its own single-table
# request (the whitelist forbids JOINs and IN-list literals against another
# query's results).  Joins happen locally in DuckDB after Parquet readback.
#
# Tables involved:
#   - pg_index      indrelid → table oid; indkey int2vector → ordinal positions
#                   of indexed columns; indisunique / indisprimary flags
#   - pg_attribute  attrelid → table oid; attnum → ordinal; attname → column
#   - pg_class      oid → relname (table name); relnamespace → namespace oid
#   - pg_namespace  oid → nspname (schema name)
#
# `attisdropped = false` is a literal-WHERE filter and is whitelisted.
# pg_class/pg_index are pulled unfiltered (small catalog tables, ~10K rows
# each in a large database).
# ---------------------------------------------------------------------------

_PG_INDEX_QUERY = (
    "SELECT indrelid, indkey, indisunique, indisprimary "
    "FROM pg_catalog.pg_index"
)

_PG_ATTRIBUTE_QUERY = (
    "SELECT attrelid, attnum, attname "
    "FROM pg_catalog.pg_attribute "
    "WHERE attisdropped = false"
)

_PG_CLASS_QUERY = (
    "SELECT oid, relname, relnamespace "
    "FROM pg_catalog.pg_class"
)

_PG_NAMESPACE_QUERY = (
    "SELECT oid, nspname "
    "FROM pg_catalog.pg_namespace "
    "WHERE nspname = '{schema}'"
)


def _compute_index_flags(
    con: "duckdb.DuckDBPyConnection",
    schema: str,
    pg_index_path: str | None,
    pg_attr_path: str | None,
    pg_class_path: str | None,
    pg_ns_path: str | None,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """
    Join the four pg_catalog parquet extracts to derive per-(table, column)
    index flags for *schema*.

    Returns
    -------
    (indexed_cols, unique_indexed_cols)
        Two sets of ``(table_name, column_name)`` tuples.

    Notes
    -----
    * The ``indkey`` column from pg_index is an int2vector (rendered by the
      JDBC driver as a space-separated string of attribute numbers, e.g.
      ``"1 2"``).  Each entry is a 1-based ordinal into the indexed table's
      pg_attribute ``attnum``.  We expand it via DuckDB's ``string_split`` +
      ``unnest``.
    * Primary keys are reflected in pg_index (``indisprimary = true``).
      They are *also* indexed and (by definition) unique-indexed, so this
      function classifies PKs into both sets — which is correct for
      ``is_indexed`` / ``is_unique_indexed``.  The ``is_pk`` boolean is
      derived independently from information_schema.table_constraints.
    * If any of the four parquet paths is missing, returns empty sets — the
      caller falls back to ``False`` for both flags.
    """
    if not (pg_index_path and pg_attr_path and pg_class_path and pg_ns_path):
        return set(), set()
    if not all(
        Path(p).exists()
        for p in (pg_index_path, pg_attr_path, pg_class_path, pg_ns_path)
    ):
        return set(), set()

    # Validate schema name once more — we interpolate it into the SQL below.
    _validate_schema_name(schema)

    try:
        # indkey is int2vector — DuckDB reads it as a string like "1 2 3".
        # Split into rows, cast each to INTEGER, and join to pg_attribute.
        # Empty entries (trailing spaces) are filtered with attnum > 0.
        sql = f"""
        WITH idx AS (
            SELECT
                indrelid,
                indisunique,
                indisprimary,
                CAST(TRIM(part) AS INTEGER) AS attnum
            FROM read_parquet('{pg_index_path}')
            CROSS JOIN UNNEST(string_split(CAST(indkey AS VARCHAR), ' ')) AS t(part)
            WHERE TRIM(part) <> '' AND TRIM(part) <> '0'
        ),
        ns AS (
            SELECT oid AS nsoid
            FROM read_parquet('{pg_ns_path}')
            WHERE nspname = '{schema}'
        ),
        cls AS (
            SELECT c.oid AS reloid, c.relname
            FROM read_parquet('{pg_class_path}') c
            JOIN ns ON c.relnamespace = ns.nsoid
        ),
        att AS (
            SELECT attrelid, attnum, attname
            FROM read_parquet('{pg_attr_path}')
        )
        SELECT
            cls.relname AS table_name,
            att.attname AS column_name,
            BOOL_OR(idx.indisunique OR idx.indisprimary) AS unique_flag
        FROM idx
        JOIN cls ON cls.reloid = idx.indrelid
        JOIN att ON att.attrelid = idx.indrelid
                AND att.attnum   = idx.attnum
        GROUP BY cls.relname, att.attname
        """
        rows = con.execute(sql).fetchall()
    except Exception as exc:
        log.warning("inventory.index_join_failed", schema=schema, reason=str(exc))
        return set(), set()

    indexed: set[tuple[str, str]] = set()
    unique_indexed: set[tuple[str, str]] = set()
    for table_name, column_name, unique_flag in rows:
        key = (table_name, column_name)
        indexed.add(key)
        if unique_flag:
            unique_indexed.add(key)
    return indexed, unique_indexed


def _validate_schema_name(schema: str) -> None:
    """
    Defence-in-depth: schema names are interpolated into SQL because the
    extraction service does not accept bound parameters.  Reject any schema
    name that does not match the Postgres identifier grammar.
    """
    import re

    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", schema):
        raise ValueError(
            f"Invalid schema name {schema!r} — must match [A-Za-z_][A-Za-z0-9_]*"
        )


# ---------------------------------------------------------------------------
# Inventory helper class (internal to this module).
# ---------------------------------------------------------------------------


class _InventoryRunner:
    """
    Internal orchestration helper for Phase 1.  Public surface is
    :func:`run_phase_1` below.
    """

    def __init__(
        self,
        extraction_client: ExtractionClient,
        engine: Any,
        run_log: RunLog,
        source_conn_config: Any,
        storage_base_path: str,
        schemas: list[str],
    ) -> None:
        self._client = extraction_client
        self._engine = engine
        self._run_log = run_log
        self._conn_config = source_conn_config
        self._base_path = Path(storage_base_path)
        self._schemas = schemas

    def run(self) -> None:
        """Inventory every configured schema (idempotent)."""
        for schema in self._schemas:
            _validate_schema_name(schema)
            log.info("inventory.schema_start", schema=schema)
            self._inventory_schema(schema)
            log.info("inventory.schema_done", schema=schema)

    def _inventory_schema(self, schema: str) -> None:
        meta_dir = self._base_path / "_meta" / schema
        meta_dir.mkdir(parents=True, exist_ok=True)

        log.info("inventory.extracting_metadata", schema=schema)

        # 1. Tables
        tables_path = str(meta_dir / "tables.parquet")
        self._extract_meta(
            _TABLES_QUERY.format(schema=schema),
            tables_path,
            f"inventory.tables.{schema}",
        )

        # 2. Columns
        columns_path = str(meta_dir / "columns.parquet")
        self._extract_meta(
            _COLUMNS_QUERY.format(schema=schema),
            columns_path,
            f"inventory.columns.{schema}",
        )

        # 3. pg_stats (best-effort — may be empty for non-superusers)
        stats_path: str | None = str(meta_dir / "pg_stats.parquet")
        try:
            self._extract_meta(
                _STATS_QUERY.format(schema=schema),
                stats_path,
                f"inventory.stats.{schema}",
            )
        except Exception as exc:
            log.warning(
                "inventory.stats_unavailable",
                schema=schema,
                reason=str(exc),
            )
            stats_path = None

        # 4. table_constraints (for PK detection — best-effort)
        tc_path: str | None = str(meta_dir / "table_constraints.parquet")
        try:
            self._extract_meta(
                _TABLE_CONSTRAINTS_QUERY.format(schema=schema),
                tc_path,
                f"inventory.table_constraints.{schema}",
            )
        except Exception as exc:
            log.warning("inventory.tc_unavailable", schema=schema, reason=str(exc))
            tc_path = None

        # 5. key_column_usage (joined to table_constraints in Python)
        kcu_path: str | None = str(meta_dir / "key_column_usage.parquet")
        try:
            self._extract_meta(
                _KEY_COLUMN_USAGE_QUERY.format(schema=schema),
                kcu_path,
                f"inventory.key_column_usage.{schema}",
            )
        except Exception as exc:
            log.warning("inventory.kcu_unavailable", schema=schema, reason=str(exc))
            kcu_path = None

        # 6. Index discovery — pg_index, pg_attribute, pg_class, pg_namespace.
        # Each is a single-table SELECT that the whitelist accepts; joins
        # happen locally in DuckDB.  All four are best-effort: if any fails
        # (insufficient privileges on a managed Postgres, e.g. RDS without
        # superuser), index info defaults to False rather than aborting.
        pg_index_path: str | None = str(meta_dir / "pg_index.parquet")
        try:
            self._extract_meta(
                _PG_INDEX_QUERY,
                pg_index_path,
                f"inventory.pg_index.{schema}",
            )
        except Exception as exc:
            log.warning("inventory.pg_index_unavailable", schema=schema, reason=str(exc))
            pg_index_path = None

        pg_attr_path: str | None = str(meta_dir / "pg_attribute.parquet")
        try:
            self._extract_meta(
                _PG_ATTRIBUTE_QUERY,
                pg_attr_path,
                f"inventory.pg_attribute.{schema}",
            )
        except Exception as exc:
            log.warning(
                "inventory.pg_attribute_unavailable", schema=schema, reason=str(exc)
            )
            pg_attr_path = None

        pg_class_path: str | None = str(meta_dir / "pg_class.parquet")
        try:
            self._extract_meta(
                _PG_CLASS_QUERY,
                pg_class_path,
                f"inventory.pg_class.{schema}",
            )
        except Exception as exc:
            log.warning("inventory.pg_class_unavailable", schema=schema, reason=str(exc))
            pg_class_path = None

        pg_ns_path: str | None = str(meta_dir / "pg_namespace.parquet")
        try:
            self._extract_meta(
                _PG_NAMESPACE_QUERY.format(schema=schema),
                pg_ns_path,
                f"inventory.pg_namespace.{schema}",
            )
        except Exception as exc:
            log.warning(
                "inventory.pg_namespace_unavailable", schema=schema, reason=str(exc)
            )
            pg_ns_path = None

        # 7. Process results locally with DuckDB
        self._process_parquet(
            schema=schema,
            tables_path=tables_path,
            columns_path=columns_path,
            stats_path=stats_path,
            tc_path=tc_path,
            kcu_path=kcu_path,
            pg_index_path=pg_index_path,
            pg_attr_path=pg_attr_path,
            pg_class_path=pg_class_path,
            pg_ns_path=pg_ns_path,
        )

    def _extract_meta(self, query: str, output_path: str, tag: str) -> None:
        """Submit a metadata extraction request to the service."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        req = ExtractionRequest(
            connection=self._conn_config,
            query=query,
            output=OutputConfig(path=output_path, compression="zstd"),
            options=ExtractionOptions(fetch_size=10_000, tag=tag),
        )
        response = self._client.extract_sync(req)
        if not response.manifest or not response.manifest.files:
            raise RuntimeError(f"No output files returned for query tag={tag}")

    def _process_parquet(
        self,
        schema: str,
        tables_path: str,
        columns_path: str,
        stats_path: str | None,
        tc_path: str | None,
        kcu_path: str | None,
        pg_index_path: str | None = None,
        pg_attr_path: str | None = None,
        pg_class_path: str | None = None,
        pg_ns_path: str | None = None,
    ) -> None:
        """
        Read the extracted Parquet files with DuckDB and write to results DB.
        """
        con = duckdb.connect(":memory:")

        # Load tables (filter for BASE TABLE — the source of truth lives in
        # information_schema.tables but the whitelist forbids extra WHERE clauses
        # other than the schema match; filter in Python).
        tables_rows = con.execute(
            f"SELECT table_name, table_type FROM read_parquet('{tables_path}')"
        ).fetchall()
        base_tables: list[str] = [
            row[0] for row in tables_rows if str(row[1]).upper() == "BASE TABLE"
        ]

        # Load columns
        columns_df = con.execute(
            f"SELECT * FROM read_parquet('{columns_path}')"
        ).df()

        # PK detection: join table_constraints + key_column_usage in DuckDB.
        pk_cols: set[tuple[str, str]] = set()
        if tc_path and kcu_path and Path(tc_path).exists() and Path(kcu_path).exists():
            try:
                rows = con.execute(
                    f"""
                    SELECT kcu.table_name, kcu.column_name
                    FROM read_parquet('{tc_path}') tc
                    JOIN read_parquet('{kcu_path}') kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                    """
                ).fetchall()
                pk_cols = {(r[0], r[1]) for r in rows}
            except Exception as exc:
                log.warning("inventory.pk_join_failed", schema=schema, reason=str(exc))

        # Stats (null_frac, n_distinct).
        stats_data: dict[tuple[str, str], dict[str, Any]] = {}
        if stats_path and Path(stats_path).exists():
            try:
                rows = con.execute(
                    f"SELECT tablename, attname, null_frac, n_distinct "
                    f"FROM read_parquet('{stats_path}')"
                ).fetchall()
                for r in rows:
                    stats_data[(r[0], r[1])] = {"null_frac": r[2], "n_distinct": r[3]}
            except Exception as exc:
                log.warning("inventory.stats_read_error", error=str(exc))

        # Index discovery: derive (table, column) → is_indexed / is_unique_indexed
        # by joining pg_index, pg_attribute, pg_class, pg_namespace in DuckDB.
        indexed_cols, unique_indexed_cols = _compute_index_flags(
            con,
            schema,
            pg_index_path,
            pg_attr_path,
            pg_class_path,
            pg_ns_path,
        )

        con.close()

        # Organise columns by table
        cols_by_table: dict[str, list[Any]] = {}
        for _, col_row in columns_df.iterrows():
            t = col_row["table_name"]
            cols_by_table.setdefault(t, []).append(col_row)

        # Write to results DB in a single transaction per schema.
        with txn(self._engine) as conn:
            tbl_dao = TblInventory(conn)
            col_dao = ColInventory(conn)

            for table_name in base_tables:
                excluded, reason = should_exclude(table_name)

                tbl_record: dict[str, Any] = {
                    "schema_name": schema,
                    "table_name": table_name,
                    "exclusion_reason": reason,
                }
                if excluded:
                    # Excluded tables get status='excluded' explicitly — the
                    # extraction phase will skip them.
                    tbl_record["status"] = "excluded"
                # For non-excluded tables we DO NOT include 'status'.  On INSERT
                # the server default 'pending' applies; on UPDATE the column is
                # preserved (TblInventory.upsert excludes 'status' from
                # update_cols), so a previously-extracted table is not reverted.

                # E4 (re-inventory un-exclude): the standard upsert protects
                # 'status' so a previously-'extracted' row keeps its state.
                # That same protection means a row stuck at 'excluded' from a
                # prior run with a stricter exclusion list will *also* never
                # be reverted.  When the re-inventory now reports the table
                # is no longer excluded (reason is None) and the existing row
                # is 'excluded', explicitly call ``unexclude`` first to flip
                # status back to 'pending' and clear exclusion_reason.  The
                # ``unexclude`` UPDATE is guarded by ``WHERE status='excluded'``
                # so it cannot accidentally downgrade an 'extracted' row.
                if not excluded:
                    existing = tbl_dao.get_by_name(schema, table_name)
                    if (
                        existing is not None
                        and existing.get("status") == "excluded"
                    ):
                        tbl_dao.unexclude(int(existing["table_id"]))
                        log.info(
                            "inventory.table_unexcluded",
                            schema=schema,
                            table=table_name,
                            table_id=int(existing["table_id"]),
                        )

                tbl_dao.upsert(tbl_record)

                if excluded:
                    log.debug(
                        "inventory.table_excluded",
                        schema=schema,
                        table=table_name,
                        reason=reason,
                    )
                    continue

                # Fetch the table_id we just inserted/updated
                row_back = tbl_dao.get_by_name(schema, table_name)
                if row_back is None:
                    log.error(
                        "inventory.table_id_missing",
                        schema=schema,
                        table=table_name,
                    )
                    continue

                table_id: int = row_back["table_id"]

                # Write columns
                import pandas as _pd
                for col_row in cols_by_table.get(table_name, []):
                    def _na_to_none(v):
                        # pandas Series row values can be pd.NA / NaN; psycopg2 needs None.
                        if v is None:
                            return None
                        try:
                            if _pd.isna(v):
                                return None
                        except (TypeError, ValueError):
                            pass
                        return v

                    col_name: str = col_row["column_name"]
                    data_type: str = col_row["data_type"]
                    cml = _na_to_none(col_row.get("character_maximum_length"))
                    max_len: int | None = int(cml) if cml is not None else None
                    type_cls: TypeClass = classify_pg_type(data_type, max_len)
                    fk_eligible: bool = is_fk_eligible(type_cls)

                    # Promote columns that are structural identifiers even when
                    # their underlying type would normally be excluded
                    # (notably STRING_LONG: UUID/text-keyed primary keys are
                    # legitimate FK targets in many production schemas).
                    # Two structural signals qualify:
                    #   1. declared PK or unique index
                    #   2. column name matches FK naming convention (`id` or
                    #      `<x>_id`) AND type is STRING_LONG (we keep the
                    #      original exclusion list for BOOL/FLOAT/BINARY/JSONB
                    #      where the convention is not load-bearing)
                    is_pk_or_unique = (
                        (table_name, col_name) in pk_cols
                        or (table_name, col_name) in unique_indexed_cols
                    )
                    cl = col_name.lower()
                    is_id_named = (cl == "id" or cl.endswith("_id"))
                    if not fk_eligible and (
                        is_pk_or_unique
                        or (is_id_named and type_cls == TypeClass.STRING_LONG)
                    ):
                        fk_eligible = True

                    col_stats = stats_data.get((table_name, col_name), {})
                    null_frac: float | None = _na_to_none(col_stats.get("null_frac"))
                    n_distinct_raw = _na_to_none(col_stats.get("n_distinct"))
                    distinct_count: int | None = None
                    if n_distinct_raw is not None and n_distinct_raw > 0:
                        distinct_count = int(n_distinct_raw)

                    col_record: dict[str, Any] = {
                        "table_id": table_id,
                        "column_name": col_name,
                        "ordinal_position": int(col_row["ordinal_position"]),
                        "data_type": data_type,
                        "type_class": type_cls.value,
                        "is_nullable": col_row["is_nullable"].upper() == "YES"
                        if isinstance(col_row["is_nullable"], str)
                        else bool(col_row["is_nullable"]),
                        "is_pk": (table_name, col_name) in pk_cols,
                        "is_unique_indexed": (table_name, col_name) in unique_indexed_cols,
                        "is_indexed": (table_name, col_name) in indexed_cols,
                        "is_fk_eligible": fk_eligible,
                        "max_length": max_len,
                        "null_pct": null_frac,
                        "distinct_count": distinct_count,
                    }
                    col_dao.upsert(col_record)

                log.debug(
                    "inventory.table_processed",
                    schema=schema,
                    table=table_name,
                    table_id=table_id,
                    column_count=len(cols_by_table.get(table_name, [])),
                )

        log.info(
            "inventory.schema_written",
            schema=schema,
            total_tables=len(base_tables),
        )


# ---------------------------------------------------------------------------
# Public Phase 1 entry point
# ---------------------------------------------------------------------------


def run_phase_1(
    engine: Any,
    extraction_client: ExtractionClient,
    config: Any,
) -> None:
    """
    Inventory all configured source schemas.

    Idempotent: re-runs upsert into tbl_inventory / col_inventory.  The status
    column is not overwritten on conflict, so a table that has progressed past
    'pending' (e.g. 'extracted') keeps its current status.

    NOTE: this function does NOT write to run_log itself; the orchestrator
    owns the global-scope run_log lifecycle (start/succeed/fail) for every
    phase.  When invoked directly via the CLI, the ``is_complete`` guard
    below still short-circuits a re-run.
    """
    run_log = RunLog(engine)

    if run_log.is_complete("inventory", "global", None):
        log.info("inventory.skip_complete")
        return

    src_cfg = config.source_db
    storage_cfg = config.storage

    runner = _InventoryRunner(
        extraction_client=extraction_client,
        engine=engine,
        run_log=run_log,
        source_conn_config=src_cfg.to_connection_config(),
        storage_base_path=storage_cfg.base_path,
        schemas=src_cfg.schemas,
    )

    runner.run()
    log.info("inventory.complete")
