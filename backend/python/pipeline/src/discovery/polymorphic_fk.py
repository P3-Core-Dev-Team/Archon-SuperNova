"""
Phase 4c -- Polymorphic foreign-key detection.

Detects Rails / Django / Laravel-style polymorphic associations: a single
table carries a ``<x>_type`` or ``<x>_kind`` discriminator column alongside
a ``<x>_id`` foreign id column.  The discriminator selects which parent
table the id column joins to:

    comments(commentable_type, commentable_id, body)
        commentable_type='Post'    -> commentable_id references posts.id
        commentable_type='Article' -> commentable_id references articles.id

We emit one ``polymorphic_relationships`` row per
``(child_table, type_col, id_col, discriminator_value, parent_col)`` triple
whose containment is at least the configured floor (default 0.95).

Algorithm
---------
1. Walk ``col_inventory`` to find ``(<x>_type|kind, <x>_id)`` pairs in the
   SAME table where the type column is a short string and the id column is
   INT_NARROW / INT_WIDE / UUID.
2. For each pair, fetch the distinct discriminator values via DuckDB.  If
   the column has more than ``polymorphic_max_discriminator_distinct``
   distinct values it's not really a discriminator -- skip.
3. For each discriminator value, plural-aware-match it to a candidate
   parent table from ``tbl_inventory`` (case-insensitive plural normalize).
4. Run a single DuckDB anti-join restricted to the partitioned rows
   (``WHERE type_col = '<value>'``); keep matches with containment
   ``>= polymorphic_min_containment``.

Persistence
-----------
Writes to the new ``polymorphic_relationships`` table.  Fully idempotent --
the upsert key is
``(child_table_id, type_col_id, id_col_id, discriminator_value, parent_col_id)``.

Exports
-------
PolymorphicMatch          output dataclass
find_polymorphic_fks      pure-ish discovery function
run_phase_polymorphic_fk  orchestrator (discover + persist)
_singularize              pure helper -- documented for tests
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional

import structlog

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from discovery.config import AppConfig

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PolymorphicMatch:
    """One confirmed polymorphic FK association.

    A polymorphic association is a tuple
    ``(child_table.<id_col>, partitioned by <type_col> = '<value>')
    -> parent_table.<parent_col>``.
    """

    child_table: str
    type_column: str
    id_column: str
    discriminator_value: str
    parent_table: str
    parent_column: str
    child_distinct: int
    parent_distinct: int
    orphan_count: int
    containment_full: float

    # IDs needed for persistence (defaulted so the public dataclass shape
    # stays small for tests).
    child_table_id: Optional[int] = None
    type_col_id: Optional[int] = None
    id_col_id: Optional[int] = None
    parent_table_id: Optional[int] = None
    parent_col_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


_TYPE_SUFFIX_RE = re.compile(r"^(.+?)_(type|kind)$", re.IGNORECASE)


def _split_type_prefix(col_name: str) -> Optional[str]:
    """Return the prefix of a ``<x>_type`` / ``<x>_kind`` column, else None.

    >>> _split_type_prefix("commentable_type")
    'commentable'
    >>> _split_type_prefix("attachable_kind")
    'attachable'
    >>> _split_type_prefix("user_id") is None
    True
    """
    m = _TYPE_SUFFIX_RE.match(col_name)
    if not m:
        return None
    return m.group(1).lower()


def _singularize(name: str) -> str:
    """Return a naive singular form of *name* (lowercase).

    Handles the common English suffixes Rails / Django use for table names.
    Conservative: never under-strips ``ss``; gracefully passes through short
    names.

    >>> _singularize("posts")
    'post'
    >>> _singularize("articles")
    'article'
    >>> _singularize("categories")
    'category'
    >>> _singularize("addresses")
    'address'
    >>> _singularize("status")
    'status'
    """
    n = name.lower()
    if len(n) > 3 and n.endswith("ies"):
        return n[:-3] + "y"
    if len(n) > 2 and n.endswith("es") and not n.endswith("ses"):
        return n[:-2]
    if len(n) > 1 and n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n


def _pluralize(name: str) -> str:
    """Return a naive plural form of *name* (lowercase).

    >>> _pluralize("post")
    'posts'
    >>> _pluralize("article")
    'articles'
    >>> _pluralize("category")
    'categories'
    """
    n = name.lower()
    if n.endswith("y") and len(n) > 1 and n[-2] not in "aeiou":
        return n[:-1] + "ies"
    if n.endswith(("s", "x", "z")) or n.endswith(("sh", "ch")):
        return n + "es"
    return n + "s"


def _candidate_parent_names(value: str) -> list[str]:
    """Generate candidate parent-table names for a discriminator *value*.

    Examples (input -> output set):
        "Post"     -> {"post", "posts"}
        "Article"  -> {"article", "articles"}
        "orders"   -> {"order", "orders"}
        "Category" -> {"category", "categories"}

    Returned values are lowercase.  Uniqueness preserved with insertion
    order so the matcher tries the closest form first.
    """
    if not value:
        return []
    seen: list[str] = []
    base = _singularize(value)
    plural = _pluralize(base)
    for cand in (value.lower(), base, plural):
        if cand and cand not in seen:
            seen.append(cand)
    return seen


def _parent_name_match(value: str, table_name: str) -> bool:
    """True iff *value* (a discriminator string) matches *table_name*.

    Case-insensitive, plural-aware: any of the candidate forms returned by
    :func:`_candidate_parent_names` matching the lowercase ``table_name``
    counts as a hit.  Schema prefixes in the table name (``schema.table``)
    are stripped before matching.
    """
    if not value or not table_name:
        return False
    tbl = table_name.split(".")[-1].lower()
    return tbl in _candidate_parent_names(value)


def _confidence(
    name_match_strength: float,
    containment: float,
    distinct_count: int,
) -> float:
    """Blend signals into a [0, 1] confidence score.

    The formula mirrors :func:`discovery.scoring.compute_confidence`'s spirit
    -- weighted average of independent signals.  Containment dominates;
    name strength and distinct-count provide tie-breakers.
    """
    # Distinct sub-score saturates at 1000 child values -- diminishing returns.
    distinct_score = min(distinct_count / 1000.0, 1.0)
    # 0.7 containment, 0.2 name strength, 0.1 distinct.
    return round(
        0.7 * containment + 0.2 * name_match_strength + 0.1 * distinct_score, 4
    )


# ---------------------------------------------------------------------------
# DuckDB validator (single partitioned anti-join)
# ---------------------------------------------------------------------------


def _validate_partition(
    con: object,
    child_parquet: Path,
    type_col: str,
    id_col: str,
    discriminator_value: str,
    parent_parquet: Path,
    parent_col: str,
) -> tuple[int, int, int]:
    """Count ``(child_distinct, parent_distinct, orphan_count)`` for one
    discriminator partition.  Anti-join shape mirrors composite_fk.py.

    The child set is ``DISTINCT id_col WHERE type_col = '<value>'``.
    Parent set is ``DISTINCT parent_col``.
    """
    # DuckDB single-quote escape: ' -> ''.  Discriminator values come from
    # source data so this guard is non-optional.
    safe_value = discriminator_value.replace("'", "''")
    row = con.execute(  # type: ignore[union-attr]
        f"""
        WITH
          c AS (
            SELECT DISTINCT "{id_col}" AS v
            FROM   read_parquet('{str(child_parquet)}')
            WHERE  "{type_col}" = '{safe_value}'
              AND  "{id_col}" IS NOT NULL
          ),
          p AS (
            SELECT DISTINCT "{parent_col}" AS v
            FROM   read_parquet('{str(parent_parquet)}')
            WHERE  "{parent_col}" IS NOT NULL
          )
        SELECT
          (SELECT COUNT(*) FROM c)                         AS child_distinct,
          (SELECT COUNT(*) FROM p)                         AS parent_distinct,
          (SELECT COUNT(*) FROM c LEFT JOIN p ON c.v = p.v
                  WHERE p.v IS NULL)                       AS orphan_count
        """
    ).fetchone()
    if row is None:
        return (0, 0, 0)
    return (int(row[0]), int(row[1]), int(row[2]))


def _list_distinct_values(
    con: object,
    parquet_path: Path,
    column: str,
    limit: int,
) -> list[str]:
    """List distinct non-null values of *column* up to *limit* rows."""
    rows = con.execute(  # type: ignore[union-attr]
        f"""
        SELECT DISTINCT "{column}"
        FROM   read_parquet('{str(parquet_path)}')
        WHERE  "{column}" IS NOT NULL
        LIMIT  {limit + 1}
        """
    ).fetchall()
    return [str(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Discovery -- iterate the inventory and validate every pair
# ---------------------------------------------------------------------------


def _load_pairs_and_parents(engine: "Engine") -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Read tables/columns once.  Returns ``(type_id_pairs, parent_pks)``.

    ``type_id_pairs`` is one entry per matched ``(<x>_type, <x>_id)`` column
    pair on the same table.  ``parent_pks`` is one entry per single-column
    PK in ``col_inventory`` -- the polymorphic detector only joins to
    single-column PKs (composite PKs are handled by composite_fk.py).
    """
    from sqlalchemy import and_, select

    from discovery.results_db import (
        col_inventory_t,
        tbl_inventory_t,
    )

    with engine.connect() as conn:
        cols = conn.execute(
            select(
                col_inventory_t.c.column_id,
                col_inventory_t.c.table_id,
                col_inventory_t.c.column_name,
                col_inventory_t.c.data_type,
                col_inventory_t.c.type_class,
                col_inventory_t.c.is_pk,
                col_inventory_t.c.distinct_count,
                tbl_inventory_t.c.schema_name,
                tbl_inventory_t.c.table_name,
                tbl_inventory_t.c.parquet_path,
            )
            .join(
                tbl_inventory_t,
                tbl_inventory_t.c.table_id == col_inventory_t.c.table_id,
            )
            .where(tbl_inventory_t.c.parquet_path.is_not(None))
        ).mappings().all()

    cols_list = [dict(c) for c in cols]

    # Group by table_id for the type/id pairing scan.
    by_table: dict[int, list[dict[str, Any]]] = {}
    for c in cols_list:
        by_table.setdefault(int(c["table_id"]), []).append(c)

    # Build type/id pairs.
    pairs: list[dict[str, Any]] = []
    string_classes = {"STRING_SHORT"}
    id_classes = {"INT_NARROW", "INT_WIDE", "UUID"}
    for tid, columns in by_table.items():
        # Index id-suffix columns first.
        id_by_prefix: dict[str, dict[str, Any]] = {}
        for col in columns:
            name = str(col["column_name"]).lower()
            if name.endswith("_id"):
                prefix = name[: -len("_id")]
                # Only keep id-typed columns.
                if str(col.get("type_class") or "") in id_classes:
                    id_by_prefix[prefix] = col

        for col in columns:
            name = str(col["column_name"]).lower()
            prefix = _split_type_prefix(name)
            if not prefix:
                continue
            id_col = id_by_prefix.get(prefix)
            if not id_col:
                continue
            if str(col.get("type_class") or "") not in string_classes:
                # Discriminator must be a short string (varchar/text-short).
                continue
            pairs.append(
                {
                    "table_id": tid,
                    "schema_name": col["schema_name"],
                    "table_name": col["table_name"],
                    "parquet_path": col["parquet_path"],
                    "type_col_id": int(col["column_id"]),
                    "type_col_name": str(col["column_name"]),
                    "id_col_id": int(id_col["column_id"]),
                    "id_col_name": str(id_col["column_name"]),
                }
            )

    # Single-column PKs candidates for parent matching.
    parent_pks: list[dict[str, Any]] = []
    pk_count_by_table: dict[int, int] = {}
    for c in cols_list:
        if c.get("is_pk"):
            tid = int(c["table_id"])
            pk_count_by_table[tid] = pk_count_by_table.get(tid, 0) + 1
    for c in cols_list:
        if not c.get("is_pk"):
            continue
        tid = int(c["table_id"])
        if pk_count_by_table.get(tid, 0) != 1:
            # Skip composite PKs -- they are handled by composite_fk.
            continue
        if str(c.get("type_class") or "") not in id_classes:
            continue
        parent_pks.append(
            {
                "table_id": tid,
                "schema_name": c["schema_name"],
                "table_name": c["table_name"],
                "parquet_path": c["parquet_path"],
                "parent_col_id": int(c["column_id"]),
                "parent_col_name": str(c["column_name"]),
            }
        )

    return pairs, parent_pks


def find_polymorphic_fks(
    engine: "Engine",
    config: "AppConfig",
    *,
    min_containment: float = 0.95,
    max_discriminator_distinct: int = 20,
    min_partition_rows: int = 1,
) -> list[PolymorphicMatch]:
    """Discover polymorphic FK associations in the inventoried schemas.

    Returns the full list of confirmed matches (caller persists).  Each
    match is a single ``(child_table, type, id, value, parent)`` triple --
    a polymorphic association with N candidate parents produces N rows.

    Algorithm summary
    -----------------
    * For each ``(<x>_type, <x>_id)`` pair in the same table (the type col
      classified STRING_SHORT, the id col classified INT_*/UUID):
        1. List distinct discriminator values via DuckDB.
        2. Drop pairs whose discriminator has > max_discriminator_distinct
           distinct values (not really a type tag).
        3. For each value, list parent tables whose name matches the value
           (case-insensitive plural-aware).
        4. For each (value, parent) pair, run one anti-join restricted to
           ``type_col = value``; keep iff containment >= min_containment.
    """
    import duckdb  # noqa: PLC0415

    rel_cfg = getattr(config, "relationships", None)
    storage_cfg = getattr(config, "storage", None)

    pairs, parent_pks = _load_pairs_and_parents(engine)
    log.info(
        "phase4c_polymorphic.candidates",
        type_id_pairs=len(pairs),
        parent_pks=len(parent_pks),
    )
    if not pairs or not parent_pks:
        return []

    # Index parents by candidate-name forms for O(1) lookup per discriminator.
    parents_by_name: dict[str, list[dict[str, Any]]] = {}
    for pp in parent_pks:
        tbl = str(pp["table_name"]).split(".")[-1].lower()
        parents_by_name.setdefault(tbl, []).append(pp)

    confirmed: list[PolymorphicMatch] = []

    con = duckdb.connect()
    try:
        if storage_cfg is not None:
            mem_limit = getattr(storage_cfg, "duckdb_memory_limit", None)
            if mem_limit:
                try:
                    con.execute(f"SET memory_limit = '{mem_limit}'")
                except Exception:
                    pass
            temp_dir = getattr(storage_cfg, "duckdb_temp_dir", None)
            if temp_dir:
                try:
                    con.execute(f"SET temp_directory = '{temp_dir}'")
                except Exception:
                    pass
        try:
            con.execute("SET enable_object_cache=true")
        except Exception:
            pass

        for pair in pairs:
            child_path = Path(str(pair["parquet_path"]))
            type_col = str(pair["type_col_name"])
            id_col = str(pair["id_col_name"])
            try:
                values = _list_distinct_values(
                    con, child_path, type_col, max_discriminator_distinct
                )
            except Exception as exc:  # pragma: no cover - parquet read errors
                log.warning(
                    "phase4c_polymorphic.list_values_failed",
                    table=pair["table_name"],
                    type_col=type_col,
                    error=str(exc),
                )
                continue
            if len(values) > max_discriminator_distinct:
                log.debug(
                    "phase4c_polymorphic.skip_high_cardinality_discriminator",
                    table=pair["table_name"],
                    type_col=type_col,
                    distinct=len(values),
                )
                continue
            log.info(
                "phase4c_polymorphic.partitioning",
                table=pair["table_name"],
                type_col=type_col,
                values=values,
            )

            for value in values:
                # Look up candidate parents from the index.
                candidates: list[dict[str, Any]] = []
                seen_parent_ids: set[int] = set()
                for cand_name in _candidate_parent_names(value):
                    for pp in parents_by_name.get(cand_name, []):
                        # Same schema preference: only accept parents in the
                        # same schema as the child to avoid cross-schema
                        # noise (saleor + dvdrental in same DB, etc.).
                        if pp["schema_name"] != pair["schema_name"]:
                            continue
                        if pp["parent_col_id"] in seen_parent_ids:
                            continue
                        seen_parent_ids.add(pp["parent_col_id"])
                        candidates.append(pp)
                if not candidates:
                    continue

                for parent in candidates:
                    parent_path = Path(str(parent["parquet_path"]))
                    try:
                        cd, pd, orphans = _validate_partition(
                            con,
                            child_path,
                            type_col,
                            id_col,
                            value,
                            parent_path,
                            str(parent["parent_col_name"]),
                        )
                    except Exception as exc:
                        log.warning(
                            "phase4c_polymorphic.validate_failed",
                            child_table=pair["table_name"],
                            parent_table=parent["table_name"],
                            value=value,
                            error=str(exc),
                        )
                        continue

                    if cd < min_partition_rows:
                        continue
                    containment = (1.0 - orphans / cd) if cd > 0 else 0.0
                    containment = round(containment, 4)
                    if containment < min_containment:
                        continue

                    confirmed.append(
                        PolymorphicMatch(
                            child_table=str(pair["table_name"]),
                            type_column=type_col,
                            id_column=id_col,
                            discriminator_value=value,
                            parent_table=str(parent["table_name"]),
                            parent_column=str(parent["parent_col_name"]),
                            child_distinct=cd,
                            parent_distinct=pd,
                            orphan_count=orphans,
                            containment_full=containment,
                            child_table_id=int(pair["table_id"]),
                            type_col_id=int(pair["type_col_id"]),
                            id_col_id=int(pair["id_col_id"]),
                            parent_table_id=int(parent["table_id"]),
                            parent_col_id=int(parent["parent_col_id"]),
                        )
                    )
    finally:
        con.close()

    log.info("phase4c_polymorphic.discovered", confirmed=len(confirmed))
    return confirmed


# ---------------------------------------------------------------------------
# Phase orchestrator
# ---------------------------------------------------------------------------


def run_phase_polymorphic_fk(
    engine: "Engine",
    config: "AppConfig",
) -> int:
    """Discover polymorphic FKs and persist them.

    Returns the number of rows persisted to ``polymorphic_relationships``.
    Reads tunables from ``config.relationships``:

        * ``polymorphic_min_containment`` (default 0.95)
        * ``polymorphic_max_discriminator_distinct`` (default 20)
        * ``polymorphic_min_partition_rows`` (default 1)

    Idempotent: persistence uses ON CONFLICT DO UPDATE.  Failure is
    logged and reported as zero rows persisted -- the polymorphic phase
    is additive and never blocks downstream work.
    """
    from discovery.results_db import (
        PolymorphicRelationship as PolymorphicDAO,
        txn,
    )

    rel_cfg = getattr(config, "relationships", None)
    min_containment: float = float(
        getattr(rel_cfg, "polymorphic_min_containment", 0.95)
    )
    max_discriminator_distinct: int = int(
        getattr(rel_cfg, "polymorphic_max_discriminator_distinct", 20)
    )
    min_partition_rows: int = int(
        getattr(rel_cfg, "polymorphic_min_partition_rows", 1)
    )

    matches = find_polymorphic_fks(
        engine,
        config,
        min_containment=min_containment,
        max_discriminator_distinct=max_discriminator_distinct,
        min_partition_rows=min_partition_rows,
    )
    if not matches:
        log.info("phase4c_polymorphic.nothing_to_persist")
        return 0

    written = 0
    try:
        with txn(engine) as conn:
            dao = PolymorphicDAO(conn)
            for m in matches:
                if (
                    m.child_table_id is None
                    or m.type_col_id is None
                    or m.id_col_id is None
                    or m.parent_table_id is None
                    or m.parent_col_id is None
                ):
                    continue
                evidence = {
                    "child_distinct": m.child_distinct,
                    "parent_distinct": m.parent_distinct,
                    "orphan_count": m.orphan_count,
                    "discriminator_value": m.discriminator_value,
                    "type_column": m.type_column,
                    "id_column": m.id_column,
                    "parent_table": m.parent_table,
                    "parent_column": m.parent_column,
                }
                # name_match_strength: 1.0 if exact lower-case match,
                # else 0.85 if needs plural / singular normalize.
                tbl_lc = str(m.parent_table).split(".")[-1].lower()
                name_match_strength = 1.0 if tbl_lc == m.discriminator_value.lower() else 0.85
                confidence = _confidence(
                    name_match_strength=name_match_strength,
                    containment=m.containment_full,
                    distinct_count=m.child_distinct,
                )
                dao.upsert(
                    {
                        "child_table_id": m.child_table_id,
                        "type_col_id": m.type_col_id,
                        "id_col_id": m.id_col_id,
                        "discriminator_value": m.discriminator_value,
                        "parent_table_id": m.parent_table_id,
                        "parent_col_id": m.parent_col_id,
                        "containment_full": m.containment_full,
                        "confidence": confidence,
                        "evidence": evidence,
                    }
                )
                written += 1
    except Exception as exc:
        log.error("phase4c_polymorphic.persist_failed", error=str(exc))
        return 0

    log.info(
        "phase4c_polymorphic.complete",
        confirmed=len(matches),
        persisted=written,
    )
    return written


__all__ = [
    "PolymorphicMatch",
    "find_polymorphic_fks",
    "run_phase_polymorphic_fk",
    "_split_type_prefix",
    "_singularize",
    "_pluralize",
    "_candidate_parent_names",
    "_parent_name_match",
    "_validate_partition",
    "_list_distinct_values",
    "_confidence",
]
