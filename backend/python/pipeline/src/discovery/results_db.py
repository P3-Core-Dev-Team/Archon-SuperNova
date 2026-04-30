"""
results_db.py — SQLAlchemy Core engine, schema init, and DAO layer for the
Discovery results database (Postgres, schema: discovery).

Design decisions:
  - SQLAlchemy Core (Table objects), not ORM.
  - All public upsert helpers use INSERT ... ON CONFLICT DO UPDATE
    (PostgreSQL dialect) for idempotency.
  - The context manager txn(engine) yields a Connection inside a transaction;
    the caller issues the SQL, and commit/rollback is automatic.
  - Table metadata is kept as module-level singletons (created once at import)
    so callers can reference columns without re-declaring.
"""
from __future__ import annotations

import contextlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import (
    REAL as Real,
    BigInteger,
    Boolean,
    Column,
    Connection,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    create_engine,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, insert

# TIMESTAMPTZ in Postgres = TIMESTAMP WITH TIME ZONE.
# SQLAlchemy's pg dialect exposes this as TIMESTAMP(timezone=True).
TIMESTAMPTZ = TIMESTAMP(timezone=True)
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Metadata & Table definitions (mirror results_schema.sql)
# ---------------------------------------------------------------------------

_SCHEMA = "discovery"
metadata = MetaData(schema=_SCHEMA)

tbl_inventory_t = Table(
    "tbl_inventory",
    metadata,
    Column("table_id", BigInteger, primary_key=True),
    Column("schema_name", Text, nullable=False),
    Column("table_name", Text, nullable=False),
    Column("row_count_estimate", BigInteger),
    Column("byte_size_estimate", BigInteger),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("exclusion_reason", Text),
    Column("parquet_path", Text),
    Column("parquet_bytes", BigInteger),
    Column("extracted_at", TIMESTAMPTZ),
    # Subject-kind tagging from pii_propagation: JSONB array of direct PII
    # identifier types reachable on this table (e.g. ["EMAIL","SSN_US"]).
    Column("subject_kinds", JSONB),
    # Closure depth from the nearest direct-identifier root.
    Column("subject_link_distance", Integer),
    # Clustering phase columns (additive migration; NULL until cluster phase runs).
    Column("cluster_id", BigInteger),          # FK to clusters.cluster_id (DB PK)
    Column("archetype", Text),                 # FACT|DIMENSION|LOOKUP|JUNCTION|AUDIT
    Column("junction_collapsed", Boolean, server_default=text("false")),
    Column("created_at", TIMESTAMPTZ, server_default=text("now()")),
    Column("updated_at", TIMESTAMPTZ, server_default=text("now()")),
)

col_inventory_t = Table(
    "col_inventory",
    metadata,
    Column("column_id", BigInteger, primary_key=True),
    Column("table_id", BigInteger, nullable=False),
    Column("column_name", Text, nullable=False),
    Column("ordinal_position", Integer, nullable=False),
    Column("data_type", Text, nullable=False),
    Column("type_class", Text, nullable=False),
    Column("is_nullable", Boolean, nullable=False),
    Column("is_pk", Boolean, nullable=False, server_default=text("false")),
    Column("is_unique_indexed", Boolean, nullable=False, server_default=text("false")),
    Column("is_indexed", Boolean, nullable=False, server_default=text("false")),
    Column("is_fk_eligible", Boolean, nullable=False, server_default=text("true")),
    Column("max_length", Integer),
    Column("distinct_count", BigInteger),
    Column("null_pct", Real),
    Column("min_val", Text),
    Column("max_val", Text),
    Column("cardinality_estimate", BigInteger),
    Column("cardinality_method", Text),
    Column("sketcher_kind", Text, nullable=False, server_default=text("'hyperminhash'")),
    Column("sketch_blob", LargeBinary),
    Column("fingerprinted_at", TIMESTAMPTZ),
    # Parquet physical-type family (populated post-extraction by extraction.py).
    # Lets validate.py skip per-candidate DESCRIBE round-trips (B2).  NULL for
    # columns not yet extracted.
    Column("physical_type", Text),
)

fk_candidates_t = Table(
    "fk_candidates",
    metadata,
    Column("candidate_id", BigInteger, primary_key=True),
    Column("child_col_id", BigInteger, nullable=False),
    Column("parent_col_id", BigInteger, nullable=False),
    Column("estimated_containment", Real),
    Column("name_similarity", Real),
    Column("type_match", Boolean, nullable=False),
    Column("source_stage", Text, nullable=False),
    Column("joint_estimate", BigInteger),
    # tier: 'primary' (Phase 5 validates) or 'advisory_lowconf' (Phase 5 skips).
    # Added by the FK-precision improvement pass; default 'primary' preserves
    # legacy behaviour for older client code that doesn't set the column.
    Column("tier", Text, nullable=False, server_default=text("'primary'")),
    Column("created_at", TIMESTAMPTZ, server_default=text("now()")),
)

relationships_t = Table(
    "relationships",
    metadata,
    Column("rel_id", BigInteger, primary_key=True),
    Column("child_col_id", BigInteger, nullable=False),
    Column("parent_col_id", BigInteger, nullable=False),
    Column("containment_full", Real),
    Column("cardinality", Text, nullable=False),
    Column("confidence", Real),
    Column("evidence", JSONB),
    Column("validated_locally", Boolean, nullable=False, server_default=text("true")),
    Column("validation_method", Text, nullable=False, server_default=text("'local_duckdb_full'")),
    Column("discovered_at", TIMESTAMPTZ, server_default=text("now()")),
)

pii_findings_t = Table(
    "pii_findings",
    metadata,
    Column("finding_id", BigInteger, primary_key=True),
    # column_id is nullable to allow table-level findings (IDENTITY_BUNDLE).
    # Either column_id OR table_id must be supplied; column-level findings
    # populate column_id and leave table_id NULL.
    Column("column_id", BigInteger, nullable=True),
    Column("table_id", BigInteger, nullable=True),
    Column("pii_type", Text, nullable=False),
    Column("detector", Text, nullable=False),
    Column("match_count", Integer, nullable=False),
    Column("sample_count", Integer, nullable=False),
    Column("match_rate", Real, nullable=False),
    # regex_match_rate — the raw match rate from the regex/Hyperscan pass,
    # before any validator (Luhn, stdnum, etc.) reduces it.  Lets the report
    # show why a high-match candidate was filtered out.  Added by C5.
    Column("regex_match_rate", Real),
    # name_prior — true if the column NAME suggests this PII type
    # (e.g. column 'ssn' → boost SSN_US).  Added by C5.
    # Nullable to match the DDL (BOOLEAN DEFAULT false, no NOT NULL).
    Column("name_prior", Boolean, server_default=text("false")),
    # score — combined post-validation confidence.  Added by C5.
    Column("score", Real),
    # specificity — pattern-specificity tier (lower = more specific, less
    # ambiguous).  Added by C5.
    Column("specificity", Integer),
    Column("validated", Boolean, nullable=False, server_default=text("false")),
    Column("redacted_examples", JSONB),
    # IIN/BIN provider breakdown for CC_NUMBER findings — list of
    # {"brand", "count", "share"} dicts.  NULL for non-CC findings.
    Column("provider_breakdown", JSONB),
    Column("detected_at", TIMESTAMPTZ, server_default=text("now()")),
)

run_log_t = Table(
    "run_log",
    metadata,
    Column("log_id", BigInteger, primary_key=True),
    Column("phase", Text, nullable=False),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", BigInteger, nullable=True),  # nullable in schema; use sentinel 0 for global scope
    Column("status", Text, nullable=False),
    Column("started_at", TIMESTAMPTZ, server_default=text("now()")),
    Column("ended_at", TIMESTAMPTZ),
    Column("error_message", Text),
    Column("metadata", JSONB),
)

# Phase 4b — composite (multi-column) foreign keys.
# The single-column ``relationships`` table cannot represent multi-column
# references (its UNIQUE constraint is on ``(child_col_id, parent_col_id)``),
# so composite FKs land in a parallel table keyed on the table pair plus the
# JSONB-encoded column lists.  See composite_fk.py for the discovery flow.
composite_relationships_t = Table(
    "composite_relationships",
    metadata,
    Column("composite_id", BigInteger, primary_key=True),
    Column("child_table_id", BigInteger, nullable=False),
    Column("parent_table_id", BigInteger, nullable=False),
    Column("child_columns", JSONB, nullable=False),
    Column("parent_columns", JSONB, nullable=False),
    Column("containment_full", Real),
    Column("cardinality", Text),
    Column("name_similarity", Real),
    Column("discovered_at", TIMESTAMPTZ, server_default=text("now()")),
)

# Phase post-pipeline — PII leak detection (sketch-based containment).
# Populated by ``discovery.pii_leak.run_pii_leak``.  See sql/results_schema.sql.
pii_leaks_t = Table(
    "pii_leaks",
    metadata,
    Column("leak_id", BigInteger, primary_key=True),
    Column("source_col_id", BigInteger, nullable=False),
    Column("target_col_id", BigInteger, nullable=False),
    Column("containment", Real, nullable=False),
    Column("leak_kind", Text, nullable=False, server_default=text("'value_overlap'")),
    Column("detected_at", TIMESTAMPTZ, server_default=text("now()")),
)

# Phase 4c — polymorphic foreign keys (Rails / Django / Laravel pattern).
# A pair (type_col, id_col) on the SAME child table where the discriminator
# string column selects which parent table to join on the id column.
# Each ``(child_table, type_col, id_col, discriminator_value, parent_col)``
# triple gets one row.  See ``polymorphic_fk.py`` for the discovery flow.
polymorphic_relationships_t = Table(
    "polymorphic_relationships",
    metadata,
    Column("poly_id", BigInteger, primary_key=True),
    Column("child_table_id", BigInteger, nullable=False),
    Column("type_col_id", BigInteger, nullable=False),
    Column("id_col_id", BigInteger, nullable=False),
    Column("discriminator_value", Text, nullable=False),
    Column("parent_table_id", BigInteger, nullable=False),
    Column("parent_col_id", BigInteger, nullable=False),
    Column("containment_full", Real),
    Column("confidence", Real),
    Column("evidence", JSONB),
    Column("discovered_at", TIMESTAMPTZ, server_default=text("now()")),
)

# Phase 4d — JSONB soft-FK relationships.  FK-shaped values buried inside
# JSONB columns extracted by leaf path (e.g. ``$.order_id``).  Populated
# by ``discovery.jsonb_fk.run_phase_jsonb_fk``.
jsonb_relationships_t = Table(
    "jsonb_relationships",
    metadata,
    Column("jsonb_id", BigInteger, primary_key=True),
    Column("child_col_id", BigInteger, nullable=False),
    Column("jsonb_path", Text, nullable=False),
    Column("parent_col_id", BigInteger, nullable=False),
    Column("distinct_count", BigInteger),
    Column("containment_full", Real),
    Column("confidence", Real),
    Column("evidence", JSONB),
    Column("discovered_at", TIMESTAMPTZ, server_default=text("now()")),
)

# Clustering phase — one row per discovered cluster per schema.
# ``cluster_local_id`` is CL-1's 0-indexed cluster index within a schema.
# ``cluster_id`` (BIGSERIAL PK) is assigned by Postgres; tbl_inventory rows
# carry this PK via the ``cluster_id`` FK column.
clusters_t = Table(
    "clusters",
    metadata,
    Column("cluster_id", BigInteger, primary_key=True),
    Column("schema_name", Text, nullable=False),
    Column("cluster_local_id", Integer, nullable=False),
    Column("name", Text, nullable=False),
    Column("table_count", Integer, nullable=False),
    Column("intra_edge_count", Integer, nullable=False),
    Column("inter_edge_count", Integer, nullable=False),
    Column("modularity_score", Real),
    Column("archetype_distribution", JSONB, nullable=False),
    Column("member_table_ids", JSONB, nullable=False),
    Column("generated_at", TIMESTAMPTZ, server_default=text("now()")),
)

# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def get_engine(cfg: Any, pool_size: int = 5, max_overflow: int = 10) -> Engine:
    """
    Create a SQLAlchemy engine connected to the results DB.

    Parameters
    ----------
    cfg:
        A ResultsDbConfig instance (or any object with a .dsn property).
    pool_size:
        Connection pool size.
    max_overflow:
        Extra connections allowed beyond pool_size.

    Returns
    -------
    sqlalchemy.engine.Engine
    """
    engine = create_engine(
        cfg.dsn,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,  # evict stale connections
        connect_args={"options": f"-c search_path={_SCHEMA},public"},
    )
    return engine


def init_schema(engine: Engine, schema_sql_path: Path) -> None:
    """
    Execute the results schema DDL from *schema_sql_path*.

    Idempotent — uses CREATE IF NOT EXISTS everywhere.  Typically called once
    by `discovery init`.

    Parameters
    ----------
    engine:
        Connected engine.
    schema_sql_path:
        Path to results_schema.sql (owned by another agent, not created here).
    """
    sql_text = schema_sql_path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(sql_text))


# Sprint A8 — composite-FK fold-in via read-time UNION view.
# Composite FKs land in ``composite_relationships`` (table-pair keyed,
# JSONB column lists).  The single-column ``relationships`` table is
# what the API and clustering see.  Rather than duplicate composite
# rows into ``relationships`` at write time (data drift risk on
# re-runs, harder to revert), we project both into a single view
# ``relationships_unified`` that the API queries when it exists.
#
# Composite rows emit one synthetic ``(child_col_id, parent_col_id)``
# anchored on the FIRST column of the tuple, with ``composite_columns``
# carrying the full JSONB tuple so the consumer can render the
# multi-column FK correctly.
#
# Idempotent: ``CREATE OR REPLACE`` re-runs are safe and cheap.
_RELATIONSHIPS_UNIFIED_VIEW_SQL = """
CREATE OR REPLACE VIEW discovery.relationships_unified AS
SELECT
    r.child_col_id,
    r.parent_col_id,
    r.containment_full,
    r.cardinality,
    r.confidence,
    r.evidence,
    NULL::jsonb            AS composite_columns,
    'single'::text         AS relationship_kind,
    r.discovered_at
FROM discovery.relationships r
UNION ALL
SELECT
    -- composite_relationships stores child_columns / parent_columns as
    -- JSONB arrays of column NAMES (strings).  Anchor each composite row
    -- on the FIRST column of the tuple so the API JOIN against
    -- col_inventory resolves; lookup is by (table_id, column_name).
    -- Rows whose anchor column can't be resolved are silently dropped
    -- by the INNER JOINs below (defence against orphaned composite rows
    -- after a partial re-run).
    cc_anchor.column_id     AS child_col_id,
    pc_anchor.column_id     AS parent_col_id,
    cr.containment_full,
    cr.cardinality,
    NULL::real              AS confidence,
    jsonb_build_object(
        'composite_columns', cr.child_columns,
        'parent_columns',    cr.parent_columns,
        'name_similarity',   cr.name_similarity
    )                       AS evidence,
    cr.child_columns        AS composite_columns,
    'composite'::text       AS relationship_kind,
    cr.discovered_at
FROM discovery.composite_relationships cr
JOIN discovery.col_inventory cc_anchor
  ON cc_anchor.table_id = cr.child_table_id
 AND cc_anchor.column_name = (cr.child_columns->>0)
JOIN discovery.col_inventory pc_anchor
  ON pc_anchor.table_id = cr.parent_table_id
 AND pc_anchor.column_name = (cr.parent_columns->>0);
"""


def ensure_relationships_unified_view(engine: Engine) -> None:
    """Create or refresh the ``discovery.relationships_unified`` view.

    Idempotent — uses ``CREATE OR REPLACE``.  Called from the API and
    can also be invoked from a CLI bootstrap step.  Silently no-ops if
    the underlying tables don't exist yet.

    The view UNIONs single-column ``relationships`` with composite
    ``composite_relationships`` so a single SELECT against the view
    surfaces both single and composite FKs.  Composite rows are
    anchored on the first column of the tuple and carry the full
    column list in the ``composite_columns`` JSONB column.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(_RELATIONSHIPS_UNIFIED_VIEW_SQL))
    except Exception:
        # Tables may not exist yet (fresh DB) — non-fatal.  Caller
        # falls back to querying ``relationships`` directly.
        pass


# ---------------------------------------------------------------------------
# Transaction context manager
# ---------------------------------------------------------------------------


@contextmanager
def txn(engine: Engine) -> Generator[Connection, None, None]:
    """
    Context manager yielding an open transaction.

    Usage::

        with txn(engine) as conn:
            conn.execute(...)

    Commits on clean exit, rolls back on exception.
    """
    with engine.begin() as conn:
        yield conn


# ---------------------------------------------------------------------------
# DAO helpers — one per table
# ---------------------------------------------------------------------------


class TblInventory:
    """DAO for discovery.tbl_inventory."""

    # Columns owned by phases other than inventory (Phase 2 owns the
    # extraction lifecycle; Phase 1 must not overwrite them on conflict).
    _INVENTORY_OWNED_EXCLUDED = frozenset(
        {"table_id", "schema_name", "table_name", "created_at", "status", "extracted_at"}
    )

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert or update a tbl_inventory row.

        Conflict key: (schema_name, table_name).

        Status preservation
        -------------------
        On conflict, ``status`` and ``extracted_at`` are NEVER overwritten via
        the standard upsert path — those are owned by Phase 2 (extraction).
        Phase 2 must use :meth:`mark_extracted` (or similar) to mutate them.
        """
        stmt = insert(tbl_inventory_t).values(**row)
        update_cols = {
            k: stmt.excluded[k]
            for k in row
            if k not in self._INVENTORY_OWNED_EXCLUDED
        }
        update_cols["updated_at"] = text("now()")
        stmt = stmt.on_conflict_do_update(
            index_elements=["schema_name", "table_name"],
            set_=update_cols,
        )
        self._conn.execute(stmt)

    def mark_extracted(
        self,
        schema_name: str,
        table_name: str,
        parquet_path: str,
        parquet_bytes: int | None,
        row_count_estimate: int | None,
        extracted_at: Any,
    ) -> None:
        """
        Phase 2 owns ``status='extracted'`` and ``extracted_at``.  This method
        is the sole path for setting them; the regular :meth:`upsert` excludes
        both from its update set.
        """
        from sqlalchemy import update

        stmt = (
            update(tbl_inventory_t)
            .where(
                tbl_inventory_t.c.schema_name == schema_name,
                tbl_inventory_t.c.table_name == table_name,
            )
            .values(
                status="extracted",
                extracted_at=extracted_at,
                parquet_path=parquet_path,
                parquet_bytes=parquet_bytes,
                row_count_estimate=row_count_estimate,
                updated_at=text("now()"),
            )
        )
        self._conn.execute(stmt)

    def get_by_name(
        self, schema_name: str, table_name: str
    ) -> dict[str, Any] | None:
        from sqlalchemy import select

        row = self._conn.execute(
            select(tbl_inventory_t).where(
                tbl_inventory_t.c.schema_name == schema_name,
                tbl_inventory_t.c.table_name == table_name,
            )
        ).mappings().first()
        return dict(row) if row else None

    def unexclude(self, table_id: int) -> bool:
        """
        Reset a previously-excluded table back to ``status='pending'``.

        This is the **sole** path to clear an ``'excluded'`` row's status:
        :meth:`upsert` deliberately omits ``status`` from its update set
        (Phase 2 owns the lifecycle), so removing a regex pattern from
        the exclusion list and re-running inventory would otherwise leave
        the row excluded permanently — Phase 2 would silently skip it.

        Side effects on a successful update:
        * ``status``           → ``'pending'``
        * ``exclusion_reason`` → ``NULL``
        * ``parquet_path``     → ``NULL`` (any prior parquet is stale)
        * ``parquet_bytes``    → ``NULL``
        * ``extracted_at``     → ``NULL``
        * ``updated_at``       → ``now()``

        Only updates rows where ``status='excluded'`` — calling this on a
        row in another state is a no-op (returns ``False``) so that
        Phase 2 progress is never overwritten by accident.

        Parameters
        ----------
        table_id:
            Primary key of the row to un-exclude.

        Returns
        -------
        bool
            True if exactly one row was updated; False otherwise (no row
            with that id, or row was not in ``'excluded'`` state).
        """
        from sqlalchemy import update

        stmt = (
            update(tbl_inventory_t)
            .where(
                tbl_inventory_t.c.table_id == table_id,
                tbl_inventory_t.c.status == "excluded",
            )
            .values(
                status="pending",
                exclusion_reason=None,
                parquet_path=None,
                parquet_bytes=None,
                extracted_at=None,
                updated_at=text("now()"),
            )
        )
        result = self._conn.execute(stmt)
        return result.rowcount == 1


class ColInventory:
    """DAO for discovery.col_inventory."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert or update a col_inventory row.

        Conflict key: (table_id, column_name).

        Note: callers MUST supply all NOT NULL columns
        (`ordinal_position`, `data_type`, `type_class`, `is_nullable`).
        Phase 1 (inventory) is the only producer that populates these;
        downstream phases (fingerprint, etc.) should call
        :meth:`update_fingerprint` / :meth:`update_stats` instead.
        """
        stmt = insert(col_inventory_t).values(**row)
        update_cols = {
            k: stmt.excluded[k]
            for k in row
            if k not in ("column_id", "table_id", "column_name")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["table_id", "column_name"],
            set_=update_cols,
        )
        self._conn.execute(stmt)

    def update_fingerprint(self, row: dict[str, Any]) -> None:
        """
        Plain UPDATE of fingerprint-owned columns (Phase 3a).

        The col_inventory row must already exist (Phase 1 created it).
        Conflict key: (table_id, column_name). Avoids the INSERT...ON CONFLICT
        path which would trip NOT NULL checks for `ordinal_position`,
        `data_type`, `type_class`, `is_nullable` that fingerprint doesn't
        re-supply.
        """
        from sqlalchemy import update as _update

        if "table_id" not in row or "column_name" not in row:
            raise ValueError("update_fingerprint requires table_id + column_name keys")
        target_keys = {"table_id", "column_name"}
        set_cols = {k: v for k, v in row.items() if k not in target_keys}
        stmt = (
            _update(col_inventory_t)
            .where(col_inventory_t.c.table_id == row["table_id"])
            .where(col_inventory_t.c.column_name == row["column_name"])
            .values(**set_cols)
        )
        self._conn.execute(stmt)

    def list_for_table(self, table_id: int) -> list[dict[str, Any]]:
        from sqlalchemy import select

        rows = self._conn.execute(
            select(col_inventory_t)
            .where(col_inventory_t.c.table_id == table_id)
            .order_by(col_inventory_t.c.ordinal_position)
        ).mappings().all()
        return [dict(r) for r in rows]

    def update_physical_types(
        self, table_id: int, types_by_column: dict[str, str]
    ) -> None:
        """
        Set ``physical_type`` for one or more columns of a table (Phase 2 / B2).

        Called after a successful parquet extraction; the parquet schema is
        the authoritative source for physical type and is read once via
        ``pyarrow.parquet.read_schema``.  Idempotent — re-running the
        extraction simply overwrites the same value.

        Parameters
        ----------
        table_id:
            ``col_inventory.table_id`` whose columns to update.
        types_by_column:
            Mapping of ``column_name`` → canonical UPPER-CASE physical type
            (e.g. ``{"id": "BIGINT", "email": "VARCHAR"}``).  Columns not
            present in the mapping are left untouched.
        """
        from sqlalchemy import update as _update

        if not types_by_column:
            return
        for col_name, phys_type in types_by_column.items():
            stmt = (
                _update(col_inventory_t)
                .where(col_inventory_t.c.table_id == table_id)
                .where(col_inventory_t.c.column_name == col_name)
                .values(physical_type=phys_type)
            )
            self._conn.execute(stmt)


class FkCandidate:
    """DAO for discovery.fk_candidates."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert or update an FK candidate.

        Conflict key: (child_col_id, parent_col_id).
        """
        stmt = insert(fk_candidates_t).values(**row)
        update_cols = {
            k: stmt.excluded[k]
            for k in row
            if k not in ("candidate_id", "child_col_id", "parent_col_id", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["child_col_id", "parent_col_id"],
            set_=update_cols,
        )
        self._conn.execute(stmt)


class Relationship:
    """DAO for discovery.relationships."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert or update a confirmed relationship.

        Conflict key: (child_col_id, parent_col_id).
        """
        stmt = insert(relationships_t).values(**row)
        update_cols = {
            k: stmt.excluded[k]
            for k in row
            if k not in ("rel_id", "child_col_id", "parent_col_id", "discovered_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["child_col_id", "parent_col_id"],
            set_=update_cols,
        )
        self._conn.execute(stmt)


class CompositeRelationship:
    """DAO for discovery.composite_relationships.

    Composite FKs cover multi-column references like
    ``(order_id, line_no) -> order_items(order_id, line_no)``.  The
    ``child_columns`` / ``parent_columns`` lists are positionally aligned
    and stored as JSONB so any arity can fit in a single column.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert or update a composite FK row.

        Conflict key: (child_table_id, parent_table_id, child_columns,
        parent_columns).  The JSONB columns participate in the UNIQUE
        constraint so re-running discovery on the same pair simply updates
        the most recent containment / cardinality / name_similarity values.
        """
        stmt = insert(composite_relationships_t).values(**row)
        update_cols = {
            k: stmt.excluded[k]
            for k in row
            if k
            not in (
                "composite_id",
                "child_table_id",
                "parent_table_id",
                "child_columns",
                "parent_columns",
                "discovered_at",
            )
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "child_table_id",
                "parent_table_id",
                "child_columns",
                "parent_columns",
            ],
            set_=update_cols,
        )
        self._conn.execute(stmt)


class PiiFinding:
    """DAO for discovery.pii_findings."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert a PII finding, or update if one already exists for
        (column_id, pii_type, detector).

        The results schema declares a UNIQUE (column_id, pii_type, detector)
        constraint; on conflict we replace the mutable fields with the new
        run's values so that a re-scan reflects the latest match_count,
        sample_count, match_rate, validated flag, redacted examples, and
        detection timestamp.

        Additional C5 fields ``regex_match_rate`` / ``name_prior`` / ``score``
        / ``specificity`` are updated whenever the caller supplies them.
        Older callers that omit those keys leave the existing column values
        untouched (they fall through the ``set_=`` map below).

        Table-level findings (``column_id IS NULL``, ``table_id`` set) bypass
        the regular UNIQUE constraint (which permits NULL repeats per Postgres
        rules) and use the partial unique index on
        ``(table_id, pii_type, detector) WHERE column_id IS NULL``.
        """
        stmt = insert(pii_findings_t).values(**row)
        # Mutable columns updated from the new (excluded) row.  The conflict
        # key columns (column_id, pii_type, detector) are deliberately excluded.
        # The C5 fields are conditionally added so older callers (without those
        # keys) do not accidentally overwrite the columns with NULL.
        update_cols: dict[str, Any] = {
            "match_count": stmt.excluded.match_count,
            "sample_count": stmt.excluded.sample_count,
            "match_rate": stmt.excluded.match_rate,
            "validated": stmt.excluded.validated,
            "redacted_examples": stmt.excluded.redacted_examples,
            "detected_at": stmt.excluded.detected_at,
        }
        for opt in (
            "regex_match_rate",
            "name_prior",
            "score",
            "specificity",
            "provider_breakdown",
        ):
            if opt in row:
                update_cols[opt] = stmt.excluded[opt]
        # Table-level finding — column_id is NULL; conflict on the partial
        # unique index ``idx_pii_findings_table_level``.
        if row.get("column_id") is None and row.get("table_id") is not None:
            stmt = stmt.on_conflict_do_update(
                index_elements=["table_id", "pii_type", "detector"],
                index_where=text("column_id IS NULL"),
                set_=update_cols,
            )
        else:
            stmt = stmt.on_conflict_do_update(
                index_elements=["column_id", "pii_type", "detector"],
                set_=update_cols,
            )
        self._conn.execute(stmt)


class PiiLeak:
    """DAO for discovery.pii_leaks (post-pipeline leak detection)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert a leak finding or update on
        (source_col_id, target_col_id, leak_kind) conflict.
        """
        stmt = insert(pii_leaks_t).values(**row)
        update_cols = {
            "containment": stmt.excluded.containment,
            "detected_at": stmt.excluded.detected_at,
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_col_id", "target_col_id", "leak_kind"],
            set_=update_cols,
        )
        self._conn.execute(stmt)


class PolymorphicRelationship:
    """DAO for discovery.polymorphic_relationships.

    Polymorphic FKs (Rails / Django / Laravel) carry a discriminator string
    column that selects which parent table the id column joins to.  Each
    ``(child_table, type_col, id_col, discriminator_value, parent_col)``
    triple is one row -- so ``commentable_type='Post' + commentable_id ->
    posts.id`` is one row, and ``commentable_type='Article' + ... ->
    articles.id`` is another.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert or update a polymorphic FK row.

        Conflict key: (child_table_id, type_col_id, id_col_id,
        discriminator_value, parent_col_id).
        """
        stmt = insert(polymorphic_relationships_t).values(**row)
        update_cols = {
            k: stmt.excluded[k]
            for k in row
            if k
            not in (
                "poly_id",
                "child_table_id",
                "type_col_id",
                "id_col_id",
                "discriminator_value",
                "parent_col_id",
                "discovered_at",
            )
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "child_table_id",
                "type_col_id",
                "id_col_id",
                "discriminator_value",
                "parent_col_id",
            ],
            set_=update_cols,
        )
        self._conn.execute(stmt)


class JsonbRelationship:
    """DAO for discovery.jsonb_relationships.

    Soft FKs extracted from leaf paths inside JSONB columns: e.g.
    ``events.payload->>'order_id'`` linking to ``orders.id``.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert or update a JSONB soft-FK row.

        Conflict key: (child_col_id, jsonb_path, parent_col_id).
        """
        stmt = insert(jsonb_relationships_t).values(**row)
        update_cols = {
            k: stmt.excluded[k]
            for k in row
            if k
            not in (
                "jsonb_id",
                "child_col_id",
                "jsonb_path",
                "parent_col_id",
                "discovered_at",
            )
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["child_col_id", "jsonb_path", "parent_col_id"],
            set_=update_cols,
        )
        self._conn.execute(stmt)


class RunLog:
    """
    DAO for discovery.run_log.

    See run_log.py for the higher-level RunLog class with phase/status lifecycle
    methods.  This class is the raw SQL layer.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, row: dict[str, Any]) -> None:
        """
        Insert or update a run_log row.

        Conflict key: (phase, scope_type, scope_id).
        """
        stmt = insert(run_log_t).values(**row)
        update_cols = {
            k: stmt.excluded[k]
            for k in row
            if k not in ("log_id", "phase", "scope_type", "scope_id")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["phase", "scope_type", "scope_id"],
            set_=update_cols,
        )
        self._conn.execute(stmt)


class Cluster:
    """DAO for discovery.clusters + associated tbl_inventory columns.

    Idempotent: ``clear_clusters`` DELETEs existing cluster rows for a schema
    and NULLs the tbl_inventory cluster columns before any (re-)insert, so
    running the clustering phase twice on the same data produces identical rows.

    Insert strategy
    ---------------
    ``insert_clusters`` UPSERTs on ``(schema_name, cluster_local_id)`` so that
    partial re-runs (e.g. after a crash mid-schema) simply overwrite.

    cluster_id mapping
    ------------------
    CL-1's ``Cluster.cluster_id`` is a 0-indexed local index; the DB assigns
    a BIGSERIAL PK that is what ``tbl_inventory.cluster_id`` must reference.
    ``insert_clusters`` uses ``RETURNING`` to build a ``local_id → db_pk``
    map, which ``update_table_assignments`` then resolves.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert_clusters(
        self,
        schema_name: str,
        clusters: list[dict[str, Any]],
    ) -> dict[int, int]:
        """UPSERT clusters for *schema_name* and return {cluster_local_id: cluster_id (PK)}.

        Each dict in *clusters* must contain at minimum:
            cluster_local_id, name, table_count, intra_edge_count,
            inter_edge_count, archetype_distribution, member_table_ids.
        Optional: modularity_score.

        Returns a mapping of local (0-indexed) cluster id → Postgres BIGSERIAL PK.
        This map is required to resolve ``tbl_inventory.cluster_id`` (which must
        be the PK, not the local index).
        """
        from sqlalchemy import select  # noqa: PLC0415

        local_to_pk: dict[int, int] = {}
        for c in clusters:
            row = {
                "schema_name": schema_name,
                "cluster_local_id": int(c["cluster_local_id"]),
                "name": str(c["name"]),
                "table_count": int(c["table_count"]),
                "intra_edge_count": int(c.get("intra_edge_count", 0)),
                "inter_edge_count": int(c.get("inter_edge_count", 0)),
                "modularity_score": c.get("modularity_score"),
                "archetype_distribution": c["archetype_distribution"],
                "member_table_ids": c["member_table_ids"],
            }
            stmt = insert(clusters_t).values(**row)
            update_cols = {
                k: stmt.excluded[k]
                for k in row
                if k not in ("cluster_id", "schema_name", "cluster_local_id", "generated_at")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["schema_name", "cluster_local_id"],
                set_=update_cols,
            ).returning(clusters_t.c.cluster_id, clusters_t.c.cluster_local_id)
            result = self._conn.execute(stmt)
            for db_pk, local_id in result:
                local_to_pk[int(local_id)] = int(db_pk)

        # If RETURNING didn't yield every row (e.g. no-op upsert on some
        # backends), fall back to a SELECT to fill gaps.
        missing_locals = [
            int(c["cluster_local_id"])
            for c in clusters
            if int(c["cluster_local_id"]) not in local_to_pk
        ]
        if missing_locals:
            rows = self._conn.execute(
                select(clusters_t.c.cluster_id, clusters_t.c.cluster_local_id).where(
                    clusters_t.c.schema_name == schema_name,
                    clusters_t.c.cluster_local_id.in_(missing_locals),
                )
            ).all()
            for db_pk, local_id in rows:
                local_to_pk[int(local_id)] = int(db_pk)

        return local_to_pk

    def update_table_assignments(
        self,
        assignments: list[dict[str, Any]],
        local_to_pk: dict[int, int] | None = None,
    ) -> None:
        """UPDATE tbl_inventory for each assignment dict.

        Each dict must contain: table_id, cluster_id (local or PK), archetype.
        Optional: junction_collapsed (default False).

        Parameters
        ----------
        assignments:
            Each element must have ``table_id``, ``cluster_id`` (0-indexed
            local id from CL-1), and ``archetype``.
        local_to_pk:
            Mapping returned by :meth:`insert_clusters`.  If provided,
            ``assignment["cluster_id"]`` (local index) is resolved to the DB
            PK.  If None, ``assignment["cluster_id"]`` is used as-is (assumed
            to be the DB PK already).
        """
        for a in assignments:
            db_cluster_id = (
                local_to_pk.get(int(a["cluster_id"])) if local_to_pk else a["cluster_id"]
            )
            stmt = (
                update(tbl_inventory_t)
                .where(tbl_inventory_t.c.table_id == int(a["table_id"]))
                .values(
                    cluster_id=db_cluster_id,
                    archetype=str(a["archetype"]),
                    junction_collapsed=bool(a.get("junction_collapsed", False)),
                )
            )
            self._conn.execute(stmt)

    def get_clusters(
        self, schema_name: str
    ) -> list[dict[str, Any]]:
        """Return all cluster rows for *schema_name* ordered by cluster_local_id."""
        from sqlalchemy import select  # noqa: PLC0415

        rows = self._conn.execute(
            select(clusters_t)
            .where(clusters_t.c.schema_name == schema_name)
            .order_by(clusters_t.c.cluster_local_id)
        ).mappings().all()
        return [dict(r) for r in rows]

    def clear_clusters(self, schema_name: str) -> None:
        """Delete all cluster rows and NULL tbl_inventory cluster columns for *schema_name*.

        Idempotent — safe to call before every clustering run.
        """
        from sqlalchemy import delete  # noqa: PLC0415

        # 1. NULL the tbl_inventory cluster columns for this schema.
        self._conn.execute(
            update(tbl_inventory_t)
            .where(tbl_inventory_t.c.schema_name == schema_name)
            .values(
                cluster_id=None,
                archetype=None,
                junction_collapsed=False,
            )
        )
        # 2. Delete all cluster rows for this schema.
        self._conn.execute(
            delete(clusters_t).where(clusters_t.c.schema_name == schema_name)
        )
