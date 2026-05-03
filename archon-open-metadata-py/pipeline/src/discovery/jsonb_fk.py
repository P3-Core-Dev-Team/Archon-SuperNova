"""
Phase 4d -- JSONB soft-FK detection.

Postgres JSONB columns (e.g. ``events.payload``) frequently carry FK-shaped
values buried at named leaf paths -- ``{"order_id": 12345, "user": {"id":
"abc-123"}}`` -- that the single-column FK detector cannot see because the
parent of the relationship is a leaf in a JSON document, not a column.

This module:

* Finds JSONB columns via ``data_type='jsonb'`` (or the new ``JSONB``
  type_class added by C agent).  In both cases the column lands in parquet
  as a VARCHAR holding the textual JSON.
* Samples up to ``jsonb_sample_rows`` rows per column (DuckDB
  ``USING SAMPLE``) and walks the parsed JSON to enumerate all leaf paths
  whose values look like ints or UUIDs.
* For each candidate ``(child_col, jsonb_path)`` pair, computes the
  containment of its distinct value set against every single-column PK
  in the inventory using a single DuckDB query that uses
  ``json_extract_string`` / ``json_extract`` to project the leaf.
* Persists matches whose containment is at least ``jsonb_min_containment``
  (default 0.95) into the new ``jsonb_relationships`` table.

The phase never edits ``relationships``; it is a parallel surface, the
same model as composite_fk and polymorphic_fk.

Exports
-------
JsonbMatch              output dataclass
extract_leaf_paths      pure helper: walk a parsed JSON dict
find_jsonb_fks          discovery (returns list, caller persists)
run_phase_jsonb_fk      orchestrator (discover + persist)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Optional

import structlog

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from discovery.config import AppConfig

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class JsonbMatch:
    """One confirmed JSONB soft-FK relationship.

    The relationship asserts that the values produced by extracting
    ``jsonb_path`` from the child JSONB column are contained (>= the
    configured threshold) in the parent column's distinct value set.
    """

    child_table: str
    child_column: str
    jsonb_path: str
    parent_table: str
    parent_column: str
    distinct_count: int
    parent_distinct: int
    orphan_count: int
    containment_full: float

    # IDs needed for persistence.
    child_col_id: Optional[int] = None
    parent_col_id: Optional[int] = None
    parent_table_id: Optional[int] = None
    leaf_value_kind: str = "unknown"  # "int" | "uuid" | "string"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _value_kind(v: Any) -> Optional[str]:
    """Classify a JSON leaf value as ``int``, ``uuid``, ``string``, or None.

    Returns None for nulls, booleans, floats, and structures.  We deliberately
    exclude bool and float -- FKs are never floats; bools are never id
    references.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # UUID detection
        if _UUID_RE.match(s):
            return "uuid"
        # Int-as-string detection (common in JSON payloads).
        if s.lstrip("-").isdigit():
            return "int"
        return "string"
    return None


def extract_leaf_paths(
    obj: Any, prefix: str = "$"
) -> Iterator[tuple[str, Any]]:
    """Walk a parsed JSON value, yielding ``(path, leaf)`` pairs.

    Only yields scalar leaves (numbers, strings, bools, nulls).  Arrays are
    descended; the path uses ``[*]`` to denote "any element" so a column
    holding ``{"items": [{"order_id": 1}, {"order_id": 2}]}`` gets the
    path ``$.items[*].order_id``.

    Examples
    --------
    >>> list(extract_leaf_paths({"a": 1, "b": {"c": 2}}))
    [('$.a', 1), ('$.b.c', 2)]
    >>> list(extract_leaf_paths([{"x": 1}, {"x": 2}]))
    [('$[*].x', 1), ('$[*].x', 2)]
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f"{prefix}.{key}"
            if isinstance(value, (dict, list)):
                yield from extract_leaf_paths(value, new_prefix)
            else:
                yield (new_prefix, value)
    elif isinstance(obj, list):
        new_prefix = f"{prefix}[*]"
        for value in obj:
            if isinstance(value, (dict, list)):
                yield from extract_leaf_paths(value, new_prefix)
            else:
                yield (new_prefix, value)
    else:
        yield (prefix, obj)


def _path_to_duckdb_extract(path: str, output_kind: str) -> str:
    """Translate a ``$``-rooted path into a DuckDB JSON-extract expression.

    DuckDB exposes both ``json_extract`` (returns JSON value) and
    ``json_extract_string`` (returns the unquoted string).  We use
    ``json_extract_string`` for both ``int`` and ``uuid`` kinds: the
    parent value-side is normalised by ``CAST(... AS VARCHAR)`` so the
    comparison stays within one type.

    Path translation: ``$.foo.bar`` -> ``$.foo.bar`` (DuckDB accepts the
    same JSON-Path subset for ``json_extract_string``).  Array steps
    ``[*]`` are NOT supported by ``json_extract_string`` -- caller must
    skip those paths.
    """
    return f"json_extract_string(\"{output_kind}\", '{path}')"


def _path_supports_extract(path: str) -> bool:
    """Return True iff *path* uses only nested object steps (no ``[*]``).

    DuckDB's ``json_extract_string`` doesn't support array wildcards, and
    even if it did, expanding arrays is a per-row UNNEST operation that's
    materially more expensive than scalar extraction.  We skip them.
    """
    return "[*]" not in path


# ---------------------------------------------------------------------------
# Sampling and path discovery
# ---------------------------------------------------------------------------


def _sample_jsonb_paths(
    con: object,
    parquet_path: Path,
    column: str,
    sample_rows: int,
) -> dict[str, str]:
    """Sample up to *sample_rows* rows of the JSONB column and discover the
    set of FK-shaped leaf paths.

    Returns a dict ``{path: leaf_value_kind}`` where ``leaf_value_kind`` is
    the most common kind among non-null leaves at that path.  Paths whose
    values are uniformly None / empty / boolean / float are dropped.

    Implementation uses Python-side parsing for two reasons:
        1. DuckDB's JSON path discovery (``json_keys`` / ``json_structure``)
           is per-row -- aggregating across the sample is awkward.
        2. We need to record the *kind* (int vs uuid vs string) per path,
           which is hard to do purely in SQL without round-tripping every
           leaf through ``typeof``.
    """
    rows = con.execute(  # type: ignore[union-attr]
        f"""
        SELECT "{column}"
        FROM   read_parquet('{str(parquet_path)}')
        WHERE  "{column}" IS NOT NULL
        USING  SAMPLE {sample_rows} ROWS
        """
    ).fetchall()

    path_kinds: dict[str, dict[str, int]] = {}
    for row in rows:
        raw = row[0]
        if raw is None:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        for path, value in extract_leaf_paths(parsed):
            kind = _value_kind(value)
            if kind is None:
                continue
            counts = path_kinds.setdefault(path, {})
            counts[kind] = counts.get(kind, 0) + 1

    out: dict[str, str] = {}
    for path, counts in path_kinds.items():
        # Pick the most-common id-shaped kind: int and uuid are FK-like.
        # string-only paths are dropped to keep the candidate set tight.
        if "int" in counts or "uuid" in counts:
            best = max(counts.items(), key=lambda kv: kv[1])
            out[path] = best[0]
    return out


def _validate_jsonb_path(
    con: object,
    child_parquet: Path,
    child_column: str,
    jsonb_path: str,
    parent_parquet: Path,
    parent_column: str,
) -> tuple[int, int, int]:
    """Run one anti-join: child JSON-extract values vs parent column values.

    The extracted child values are CAST to VARCHAR; the parent column is
    likewise CAST so that ``CAST(123 AS VARCHAR) = '123'`` matches.  This
    sidesteps mixed-type weirdness (parent INT vs JSON int that DuckDB
    surfaces as TEXT).

    Returns ``(child_distinct, parent_distinct, orphans)``.  Caller derives
    containment.
    """
    # Single-quote escape for JSONPath literal embedded in SQL.
    safe_path = jsonb_path.replace("'", "''")
    row = con.execute(  # type: ignore[union-attr]
        f"""
        WITH
          c AS (
            SELECT DISTINCT json_extract_string("{child_column}", '{safe_path}') AS v
            FROM   read_parquet('{str(child_parquet)}')
            WHERE  "{child_column}" IS NOT NULL
              AND  json_extract_string("{child_column}", '{safe_path}') IS NOT NULL
          ),
          p AS (
            SELECT DISTINCT CAST("{parent_column}" AS VARCHAR) AS v
            FROM   read_parquet('{str(parent_parquet)}')
            WHERE  "{parent_column}" IS NOT NULL
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


def _confidence(
    name_match_strength: float,
    containment: float,
    distinct_count: int,
) -> float:
    """Mirror polymorphic_fk._confidence -- consistent confidence formula."""
    distinct_score = min(distinct_count / 1000.0, 1.0)
    return round(
        0.7 * containment + 0.2 * name_match_strength + 0.1 * distinct_score, 4
    )


# ---------------------------------------------------------------------------
# Inventory walks
# ---------------------------------------------------------------------------


def _load_jsonb_columns_and_pks(engine: "Engine") -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Return ``(jsonb_columns, parent_pks)``.

    ``jsonb_columns`` -- columns where ``data_type='jsonb'`` OR ``data_type
    = 'json'`` OR ``type_class='JSONB'``.  Tables without parquet are
    skipped.

    ``parent_pks`` -- single-column PKs of class INT_NARROW / INT_WIDE /
    UUID.  Composite-PK tables are skipped (composite_fk handles those).
    """
    from sqlalchemy import or_, select

    from discovery.results_db import (
        col_inventory_t,
        tbl_inventory_t,
    )

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                col_inventory_t.c.column_id,
                col_inventory_t.c.table_id,
                col_inventory_t.c.column_name,
                col_inventory_t.c.data_type,
                col_inventory_t.c.type_class,
                col_inventory_t.c.is_pk,
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

    cols = [dict(r) for r in rows]

    jsonb_columns: list[dict[str, Any]] = []
    for c in cols:
        dt = str(c.get("data_type") or "").lower()
        tc = str(c.get("type_class") or "")
        if dt in ("jsonb", "json") or tc == "JSONB":
            jsonb_columns.append(c)

    pk_count_by_table: dict[int, int] = {}
    for c in cols:
        if c.get("is_pk"):
            tid = int(c["table_id"])
            pk_count_by_table[tid] = pk_count_by_table.get(tid, 0) + 1

    id_classes = {"INT_NARROW", "INT_WIDE", "UUID"}
    parent_pks: list[dict[str, Any]] = []
    for c in cols:
        if not c.get("is_pk"):
            continue
        tid = int(c["table_id"])
        if pk_count_by_table.get(tid, 0) != 1:
            continue
        if str(c.get("type_class") or "") not in id_classes:
            continue
        parent_pks.append(c)

    return jsonb_columns, parent_pks


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_jsonb_fks(
    engine: "Engine",
    config: "AppConfig",
    *,
    sample_rows: int = 1000,
    min_containment: float = 0.95,
    min_distinct_count: int = 5,
) -> list[JsonbMatch]:
    """Discover JSONB soft FKs across the inventoried schemas.

    Returns the list of confirmed matches (caller persists).  No DDL
    side-effects.

    Algorithm summary
    -----------------
    1. Find JSONB columns and single-column PKs (``_load_*``).
    2. For each JSONB column, sample rows and enumerate the leaf paths
       whose values are int or UUID.  Skip paths with ``[*]`` array steps
       (DuckDB ``json_extract_string`` doesn't accept them).
    3. For each path, run an anti-join against every PK of compatible
       value-kind:
            int-shaped paths    -> INT_NARROW / INT_WIDE PKs
            uuid-shaped paths   -> UUID PKs
       Both kinds also accept STRING_SHORT PKs whose values look like ids
       (rare but valid).
    4. Keep ``(path, parent)`` pairs whose containment is at least
       ``min_containment`` and child_distinct is at least
       ``min_distinct_count``.
    """
    import duckdb  # noqa: PLC0415

    jsonb_cols, parent_pks = _load_jsonb_columns_and_pks(engine)
    log.info(
        "phase4d_jsonb.candidates",
        jsonb_columns=len(jsonb_cols),
        parent_pks=len(parent_pks),
    )
    if not jsonb_cols or not parent_pks:
        return []

    # Index parents by type_class for fast lookup.
    parents_by_kind: dict[str, list[dict[str, Any]]] = {}
    for pp in parent_pks:
        tc = str(pp.get("type_class") or "")
        parents_by_kind.setdefault(tc, []).append(pp)

    confirmed: list[JsonbMatch] = []

    storage_cfg = getattr(config, "storage", None)
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

        for child in jsonb_cols:
            child_path = Path(str(child["parquet_path"]))
            child_col = str(child["column_name"])
            log.info(
                "phase4d_jsonb.scan",
                table=child["table_name"],
                column=child_col,
            )
            try:
                paths = _sample_jsonb_paths(
                    con, child_path, child_col, sample_rows
                )
            except Exception as exc:
                log.warning(
                    "phase4d_jsonb.sample_failed",
                    table=child["table_name"],
                    column=child_col,
                    error=str(exc),
                )
                continue

            for path, kind in paths.items():
                if not _path_supports_extract(path):
                    continue

                # Choose candidate parents based on leaf kind.
                if kind == "int":
                    candidates = (
                        parents_by_kind.get("INT_NARROW", [])
                        + parents_by_kind.get("INT_WIDE", [])
                    )
                elif kind == "uuid":
                    candidates = (
                        parents_by_kind.get("UUID", [])
                        + parents_by_kind.get("STRING_SHORT", [])
                    )
                else:
                    continue

                for parent in candidates:
                    parent_path = Path(str(parent["parquet_path"]))
                    parent_col = str(parent["column_name"])
                    try:
                        cd, pd, orphans = _validate_jsonb_path(
                            con,
                            child_path,
                            child_col,
                            path,
                            parent_path,
                            parent_col,
                        )
                    except Exception as exc:
                        log.debug(
                            "phase4d_jsonb.validate_failed",
                            child=child["table_name"],
                            path=path,
                            parent=parent["table_name"],
                            error=str(exc),
                        )
                        continue

                    if cd < min_distinct_count:
                        continue
                    containment = (1.0 - orphans / cd) if cd > 0 else 0.0
                    containment = round(containment, 4)
                    if containment < min_containment:
                        continue

                    confirmed.append(
                        JsonbMatch(
                            child_table=str(child["table_name"]),
                            child_column=child_col,
                            jsonb_path=path,
                            parent_table=str(parent["table_name"]),
                            parent_column=parent_col,
                            distinct_count=cd,
                            parent_distinct=pd,
                            orphan_count=orphans,
                            containment_full=containment,
                            child_col_id=int(child["column_id"]),
                            parent_col_id=int(parent["column_id"]),
                            parent_table_id=int(parent["table_id"]),
                            leaf_value_kind=kind,
                        )
                    )
    finally:
        con.close()

    log.info("phase4d_jsonb.discovered", confirmed=len(confirmed))
    return confirmed


# ---------------------------------------------------------------------------
# Phase orchestrator
# ---------------------------------------------------------------------------


def run_phase_jsonb_fk(
    engine: "Engine",
    config: "AppConfig",
) -> int:
    """Discover JSONB soft FKs and persist them.

    Returns the number of rows persisted to ``jsonb_relationships``.
    Reads tunables from ``config.relationships``:

        * ``jsonb_sample_rows`` (default 1000)
        * ``jsonb_min_containment`` (default 0.95)
        * ``jsonb_min_distinct_count`` (default 5)
    """
    from discovery.results_db import (
        JsonbRelationship as JsonbDAO,
        txn,
    )

    rel_cfg = getattr(config, "relationships", None)
    sample_rows: int = int(getattr(rel_cfg, "jsonb_sample_rows", 1000))
    min_containment: float = float(
        getattr(rel_cfg, "jsonb_min_containment", 0.95)
    )
    min_distinct_count: int = int(
        getattr(rel_cfg, "jsonb_min_distinct_count", 5)
    )

    matches = find_jsonb_fks(
        engine,
        config,
        sample_rows=sample_rows,
        min_containment=min_containment,
        min_distinct_count=min_distinct_count,
    )
    if not matches:
        log.info("phase4d_jsonb.nothing_to_persist")
        return 0

    written = 0
    try:
        with txn(engine) as conn:
            dao = JsonbDAO(conn)
            for m in matches:
                if m.child_col_id is None or m.parent_col_id is None:
                    continue
                evidence = {
                    "child_distinct": m.distinct_count,
                    "parent_distinct": m.parent_distinct,
                    "orphan_count": m.orphan_count,
                    "leaf_value_kind": m.leaf_value_kind,
                    "child_table": m.child_table,
                    "child_column": m.child_column,
                    "parent_table": m.parent_table,
                    "parent_column": m.parent_column,
                }
                # Path-name strength: if the last component of the path
                # matches the parent column or table name, strong signal.
                last_segment = m.jsonb_path.rsplit(".", 1)[-1].rstrip("[*]")
                last_lc = last_segment.lower()
                col_lc = m.parent_column.lower()
                tbl_lc = m.parent_table.split(".")[-1].lower()
                strong = (
                    last_lc == col_lc
                    or last_lc.endswith("_" + col_lc)
                    or last_lc == f"{tbl_lc}_{col_lc}"
                )
                name_match_strength = 1.0 if strong else 0.6
                confidence = _confidence(
                    name_match_strength=name_match_strength,
                    containment=m.containment_full,
                    distinct_count=m.distinct_count,
                )
                dao.upsert(
                    {
                        "child_col_id": m.child_col_id,
                        "jsonb_path": m.jsonb_path,
                        "parent_col_id": m.parent_col_id,
                        "distinct_count": m.distinct_count,
                        "containment_full": m.containment_full,
                        "confidence": confidence,
                        "evidence": evidence,
                    }
                )
                written += 1
    except Exception as exc:
        log.error("phase4d_jsonb.persist_failed", error=str(exc))
        return 0

    log.info(
        "phase4d_jsonb.complete",
        confirmed=len(matches),
        persisted=written,
    )
    return written


__all__ = [
    "JsonbMatch",
    "extract_leaf_paths",
    "find_jsonb_fks",
    "run_phase_jsonb_fk",
    "_value_kind",
    "_path_supports_extract",
    "_sample_jsonb_paths",
    "_validate_jsonb_path",
    "_confidence",
]
