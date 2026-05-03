"""
Phase 4b — Composite (multi-column) foreign-key detection.

Single-column FK candidates are produced by Phase 4 (``candidates.py``); this
module groups those candidates by ``(child_table, parent_table)`` and looks
for multi-column references such as ``(order_id, line_no) -> order_items
(order_id, line_no)`` that wouldn't be apparent from the per-column joins
alone.

The detector is intentionally conservative: it proposes a composite ONLY
when every constituent single-column candidate already has high containment
(>= 0.95), because otherwise the composite is just exploration noise.  It
also deliberately skips redundant proposals — when both single-column FKs
are already 100% containment, the composite adds no new information.

Design (mirrors ``candidates.py`` / ``validate.py``)
----------------------------------------------------
* Pure helpers (``_enumerate_subsets``, ``_validate_composite_one``,
  ``_should_propose_composite``) have no SQLAlchemy / config / run_log
  imports — those are local to ``find_composite_fks`` / ``run_phase_4b_composite``.
* ONE DuckDB connection is opened in ``find_composite_fks`` and reused for
  every validation query (no Pool — composites are quadratic in singles per
  pair, but ``max_proposals_per_pair`` caps the work).
* Validation uses the same ``LEFT JOIN ... WHERE p IS NULL`` anti-join shape
  as :func:`discovery.validate.validate_one`, extended for multi-column joins.

Persistence
-----------
Composite FKs land in a NEW table ``composite_relationships`` — they are
not folded into the existing ``relationships`` table.  This keeps the Phase
5 single-column flow undisturbed while letting downstream consumers join in
composite results explicitly.

Exports
-------
CompositeFkCandidate    output dataclass
CompositeValidationResult internal validator output
find_composite_fks      pure-ish discovery (returns list, caller persists)
run_phase_4b_composite  Phase 4b orchestrator (discover + persist)
"""
from __future__ import annotations

import difflib
import itertools
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
class CompositeFkCandidate:
    """A confirmed composite FK between two tables.

    ``child_columns`` and ``parent_columns`` are positionally aligned: the
    join predicate is ``child[child_columns[i]] = parent[parent_columns[i]]``
    for every i.

    ``cardinality`` reuses the labels from the single-column validator:
    ``ONE_TO_ONE``, ``MANY_TO_ONE``, ``PARTIAL`` (or ``NO_RELATIONSHIP`` /
    ``TYPE_MISMATCH`` — those will not appear in the returned list, but the
    field is widened so the dataclass can carry them through tests).

    ``name_similarity`` is the **average** difflib ratio across the column
    pairs; the pre-filter (see ``_pair_name_similarity_floor``) and the
    final selection both work off this aggregate score.
    """

    child_table: str
    parent_table: str
    child_columns: list[str]   # ordered
    parent_columns: list[str]  # ordered
    containment: float
    cardinality: str           # ONE_TO_ONE | MANY_TO_ONE | PARTIAL
    name_similarity: float     # average across the column set

    # IDs needed for persistence; defaulted so the public dataclass shape
    # matches the spec exactly (only the trailing fields are extras).
    child_table_id: Optional[int] = None
    parent_table_id: Optional[int] = None
    child_col_ids: list[int] = field(default_factory=list)
    parent_col_ids: list[int] = field(default_factory=list)


@dataclass
class CompositeValidationResult:
    """Internal validator output (mirrors :class:`ValidationResult` shape)."""

    child_distinct: int
    parent_distinct: int
    orphan_count: int
    containment_full: float
    cardinality: str
    query_duration_ms: int


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


def _name_similarity(a: str, b: str) -> float:
    """difflib SequenceMatcher ratio — same metric used by candidates.py."""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _classify(
    cd: int, pd: int, orphans: int, containment_threshold: float
) -> tuple[float, str]:
    """Compute (containment_full, cardinality) from raw composite counts.

    Identical formula to :func:`discovery.validate._classify` so that
    composite cardinality semantics match the single-column flow.
    """
    containment_full = (1.0 - orphans / cd) if cd > 0 else 0.0
    if cd == pd and orphans == 0:
        cardinality = "ONE_TO_ONE"
    elif orphans == 0 and cd < pd:
        cardinality = "MANY_TO_ONE"
    elif orphans > 0 and containment_full >= containment_threshold:
        cardinality = "PARTIAL"
    else:
        cardinality = "NO_RELATIONSHIP"
    return round(containment_full, 4), cardinality


def _enumerate_subsets(
    singles: list[dict[str, Any]],
    arity: int,
) -> list[tuple[dict[str, Any], ...]]:
    """Enumerate ordered subsets of ``singles`` of size ``arity``.

    ``singles`` is a list of single-column FK candidate rows, all sharing
    the same ``(child_table, parent_table)``.  Output tuples are
    ``itertools.combinations``-shaped — i.e. C(len(singles), arity) tuples
    of length ``arity``, each preserving the input order.

    Pure / deterministic so that test 1 (size-2 subsets out of 3 singles
    yields C(3,2)=3) is straightforward.
    """
    if arity < 1:
        return []
    if arity > len(singles):
        return []
    return list(itertools.combinations(singles, arity))


def _should_propose_composite(
    constituent_containments: list[float],
    *,
    min_singles_containment: float = 0.95,
) -> bool:
    """Decide whether a subset is worth validating as a composite.

    Returns True iff:
      * EVERY constituent single-column FK already has containment
        >= ``min_singles_containment`` (hint 4 — be conservative: composites
        on weak singles are exploration noise), AND
      * NOT every single is at 100% containment (hint test 3 — when both
        singles are already perfect FKs the composite adds nothing; the
        join would simply restate the redundant constraint).

    The "not all 1.0" clause uses an exact equality test on rounded values;
    the upstream validator emits containments rounded to 4 decimal places
    so 1.0 means "literally no orphans on this single", which is the case
    that makes a composite redundant.
    """
    if not constituent_containments:
        return False
    if any(c < min_singles_containment for c in constituent_containments):
        return False
    if all(c >= 1.0 for c in constituent_containments):
        # Every constituent is already a perfect single-column FK — a
        # composite proposal here is structurally redundant.
        return False
    return True


def _pair_name_similarity_floor(
    child_cols: list[str], parent_cols: list[str]
) -> float:
    """Minimum per-pair name similarity across a candidate composite.

    Hint 5: composite columns should have correlated names — `(a.x, a.y) -> b(x, y)`
    where each column on each side carries a similar name.  We use the MIN
    rather than the AVG for the pre-filter so a single misaligned pair can
    veto the whole proposal cheaply.
    """
    if len(child_cols) != len(parent_cols) or not child_cols:
        return 0.0
    return min(
        _name_similarity(c, p) for c, p in zip(child_cols, parent_cols)
    )


def _avg_name_similarity(
    child_cols: list[str], parent_cols: list[str]
) -> float:
    """Average per-pair name similarity (used in the dataclass output)."""
    if len(child_cols) != len(parent_cols) or not child_cols:
        return 0.0
    sims = [_name_similarity(c, p) for c, p in zip(child_cols, parent_cols)]
    return sum(sims) / len(sims)


# ---------------------------------------------------------------------------
# DuckDB validator (multi-column anti-join)
# ---------------------------------------------------------------------------


def _validate_composite_one(
    con: object,
    child_parquet: Path,
    child_cols: list[str],
    parent_parquet: Path,
    parent_cols: list[str],
    containment_threshold: float = 0.95,
) -> CompositeValidationResult:
    """Validate one composite FK proposal via a single DuckDB query.

    Mirrors :func:`discovery.validate.validate_one` for multi-column joins.
    The query is wrapped in two CTEs so DuckDB sees a single anti-join plan:

    .. code-block:: sql

        WITH
          c AS (
            SELECT DISTINCT col1, col2 FROM child
            WHERE col1 IS NOT NULL AND col2 IS NOT NULL
          ),
          p AS (
            SELECT DISTINCT col1, col2 FROM parent
            WHERE col1 IS NOT NULL AND col2 IS NOT NULL
          )
        SELECT
          (SELECT COUNT(*) FROM c)                              AS child_distinct,
          (SELECT COUNT(*) FROM p)                              AS parent_distinct,
          (SELECT COUNT(*) FROM c LEFT JOIN p ON ...
                          WHERE p.<first_col> IS NULL)          AS orphan_count
    """
    if len(child_cols) != len(parent_cols) or not child_cols:
        raise ValueError(
            "child_cols and parent_cols must be non-empty and same length"
        )

    child_path = str(child_parquet)
    parent_path = str(parent_parquet)

    # Quoted column references (DuckDB uses double-quotes for identifiers).
    c_proj = ", ".join(f'"{col}"' for col in child_cols)
    p_proj = ", ".join(f'"{col}"' for col in parent_cols)
    c_not_null = " AND ".join(f'"{col}" IS NOT NULL' for col in child_cols)
    p_not_null = " AND ".join(f'"{col}" IS NOT NULL' for col in parent_cols)
    join_pred = " AND ".join(
        f'c."{cc}" = p."{pc}"' for cc, pc in zip(child_cols, parent_cols)
    )
    # Anti-join "the join missed" probe — any parent column being NULL after
    # the LEFT JOIN means no parent row matched.  Use the first parent col.
    first_parent_col = parent_cols[0]

    t0 = time.monotonic()
    row = con.execute(  # type: ignore[union-attr]
        f"""
        WITH
          c AS (
            SELECT DISTINCT {c_proj}
            FROM   read_parquet('{child_path}')
            WHERE  {c_not_null}
          ),
          p AS (
            SELECT DISTINCT {p_proj}
            FROM   read_parquet('{parent_path}')
            WHERE  {p_not_null}
          )
        SELECT
          (SELECT COUNT(*) FROM c)                              AS child_distinct,
          (SELECT COUNT(*) FROM p)                              AS parent_distinct,
          (SELECT COUNT(*) FROM c LEFT JOIN p ON {join_pred}
                          WHERE p."{first_parent_col}" IS NULL) AS orphan_count
        """
    ).fetchone()
    query_duration_ms = int((time.monotonic() - t0) * 1000)

    if row is None:
        cd = pd = orphans = 0
    else:
        cd, pd, orphans = int(row[0]), int(row[1]), int(row[2])

    containment_full, cardinality = _classify(
        cd, pd, orphans, containment_threshold
    )
    return CompositeValidationResult(
        child_distinct=cd,
        parent_distinct=pd,
        orphan_count=orphans,
        containment_full=containment_full,
        cardinality=cardinality,
        query_duration_ms=query_duration_ms,
    )


# ---------------------------------------------------------------------------
# Discovery (orchestration over the engine)
# ---------------------------------------------------------------------------


def _load_singles(
    engine: "Engine",
) -> tuple[list[dict[str, Any]], set[int]]:
    """Read primary-tier single-column FK candidates joined to col/table names.

    Returns ``(rows, pii_column_ids)`` where ``rows`` carries everything the
    composite enumerator needs to emit a tuple, and ``pii_column_ids`` is
    the set of ``column_id`` values flagged in ``pii_findings`` (used to
    skip composite proposals touching any PII column).

    The candidate must:
      * be ``tier='primary'``,
      * have ``estimated_containment >= 0.95`` (composites only built on
        already-strong singles — hint 4),
      * have parquet paths populated on both sides.
    """
    from sqlalchemy import and_, select

    from discovery.results_db import (
        col_inventory_t,
        fk_candidates_t,
        pii_findings_t,
        tbl_inventory_t,
    )

    child_col = col_inventory_t.alias("child_col")
    parent_col = col_inventory_t.alias("parent_col")
    child_tbl = tbl_inventory_t.alias("child_tbl")
    parent_tbl = tbl_inventory_t.alias("parent_tbl")

    has_tier = "tier" in fk_candidates_t.c.keys()

    select_cols = [
        fk_candidates_t.c.candidate_id,
        fk_candidates_t.c.child_col_id,
        fk_candidates_t.c.parent_col_id,
        fk_candidates_t.c.estimated_containment,
        fk_candidates_t.c.name_similarity,
        child_col.c.column_name.label("child_column_name"),
        parent_col.c.column_name.label("parent_column_name"),
        child_tbl.c.table_id.label("child_table_id"),
        child_tbl.c.table_name.label("child_table_name"),
        child_tbl.c.parquet_path.label("child_parquet_path"),
        parent_tbl.c.table_id.label("parent_table_id"),
        parent_tbl.c.table_name.label("parent_table_name"),
        parent_tbl.c.parquet_path.label("parent_parquet_path"),
    ]

    where_clauses: list[Any] = [
        child_tbl.c.parquet_path.is_not(None),
        parent_tbl.c.parquet_path.is_not(None),
    ]
    if has_tier:
        where_clauses.append(fk_candidates_t.c.tier == "primary")

    with engine.connect() as conn:
        rows = conn.execute(
            select(*select_cols)
            .join(child_col, child_col.c.column_id == fk_candidates_t.c.child_col_id)
            .join(child_tbl, child_tbl.c.table_id == child_col.c.table_id)
            .join(parent_col, parent_col.c.column_id == fk_candidates_t.c.parent_col_id)
            .join(parent_tbl, parent_tbl.c.table_id == parent_col.c.table_id)
            .where(and_(*where_clauses))
        ).mappings().all()

        pii_rows = conn.execute(
            select(pii_findings_t.c.column_id).distinct()
        ).all()

    pii_ids = {int(r[0]) for r in pii_rows}
    return [dict(r) for r in rows], pii_ids


def find_composite_fks(
    engine: "Engine",
    config: "AppConfig",
    *,
    max_arity: int = 3,
    min_containment: float = 0.95,
    max_proposals_per_pair: int = 10,
) -> list[CompositeFkCandidate]:
    """Discover composite FKs across the schema.

    Returns the list of confirmed composite candidates whose
    ``containment_full`` is at least ``min_containment``.  The caller is
    responsible for persisting them (see :func:`run_phase_4b_composite`).

    Algorithm
    ---------
    1. Load all primary-tier single-column FK candidates (with high
       per-column containment) from the results DB.
    2. Group them by ``(child_table, parent_table)``.
    3. For each group with at least 2 candidates, enumerate subsets of
       arity 2..max_arity.
    4. For each subset:
       * skip if any constituent column is flagged in ``pii_findings``,
       * skip via :func:`_should_propose_composite` (composite-on-strong-
         singles plus not-redundant gate),
       * skip if the per-pair name similarity floor is below the
         configured threshold (hint 5),
       * validate via DuckDB and keep iff containment >= ``min_containment``.

    Per-pair work is capped at ``max_proposals_per_pair`` validation
    queries; subsets are sorted by avg name similarity (descending) so the
    "best" proposals are tried first.
    """
    import duckdb  # noqa: PLC0415

    rel_cfg = getattr(config, "relationships", None)
    # Allow operators to override via config; fall back to the kwarg.
    containment_threshold: float = float(
        getattr(rel_cfg, "containment_threshold", min_containment)
    )
    # Same singles-floor as ``_should_propose_composite`` default — exposes
    # the conservative gate as a knob.
    min_singles_containment: float = float(
        getattr(rel_cfg, "composite_min_singles_containment", 0.95)
    )
    # Pre-filter floor: drop subsets whose worst per-pair name similarity is
    # below this.  Conservative default; matches the spirit of hint 5.
    name_sim_floor: float = float(
        getattr(rel_cfg, "composite_name_sim_floor", 0.5)
    )

    rows, pii_ids = _load_singles(engine)
    log.info(
        "phase4b_composite.singles_loaded",
        rows=len(rows),
        pii_columns=len(pii_ids),
    )
    if len(rows) < 2:
        return []

    # ------------------------------------------------------------------
    # Group by (child_table, parent_table).
    # ------------------------------------------------------------------
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = (str(r["child_table_name"]), str(r["parent_table_name"]))
        groups.setdefault(key, []).append(r)

    # Drop singletons — composite needs >= 2 in the group.
    candidate_groups = [
        (k, sorted(g, key=lambda x: x["child_column_name"]))
        for k, g in groups.items()
        if len(g) >= 2
    ]
    log.info(
        "phase4b_composite.groups",
        total=len(groups),
        with_two_plus=len(candidate_groups),
    )

    if not candidate_groups:
        return []

    confirmed: list[CompositeFkCandidate] = []

    # ONE DuckDB connection for all validation queries (hint 2).
    con = duckdb.connect()
    try:
        storage_cfg = getattr(config, "storage", None)
        if storage_cfg is not None:
            mem_limit = getattr(storage_cfg, "duckdb_memory_limit", None)
            if mem_limit:
                try:
                    con.execute(f"SET memory_limit = '{mem_limit}'")
                except Exception:
                    # Memory string may already be reserved by another phase;
                    # ignore failure — defaults are fine for composites.
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

        for (child_table, parent_table), singles in candidate_groups:
            # PII filter: drop singles whose child OR parent column is PII.
            singles = [
                s for s in singles
                if int(s["child_col_id"]) not in pii_ids
                and int(s["parent_col_id"]) not in pii_ids
            ]
            if len(singles) < 2:
                continue

            child_parquet = Path(str(singles[0]["child_parquet_path"]))
            parent_parquet = Path(str(singles[0]["parent_parquet_path"]))
            child_table_id = int(singles[0]["child_table_id"])
            parent_table_id = int(singles[0]["parent_table_id"])

            # Build all subset proposals (arity 2..max_arity) at once.
            proposals: list[tuple[dict[str, Any], ...]] = []
            for arity in range(2, max_arity + 1):
                proposals.extend(_enumerate_subsets(singles, arity))

            # Pre-filter / score before validation so we cap cost.
            scored: list[
                tuple[float, float, tuple[dict[str, Any], ...]]
            ] = []
            for subset in proposals:
                containments = [
                    float(s.get("estimated_containment") or 0.0) for s in subset
                ]
                if not _should_propose_composite(
                    containments,
                    min_singles_containment=min_singles_containment,
                ):
                    continue
                child_cols = [str(s["child_column_name"]) for s in subset]
                parent_cols = [str(s["parent_column_name"]) for s in subset]
                floor = _pair_name_similarity_floor(child_cols, parent_cols)
                if floor < name_sim_floor:
                    continue
                avg = _avg_name_similarity(child_cols, parent_cols)
                scored.append((avg, floor, subset))

            # Sort by avg name sim desc, then floor desc — try strongest first.
            scored.sort(key=lambda t: (-t[0], -t[1]))
            scored = scored[:max_proposals_per_pair]

            for _avg, _floor, subset in scored:
                child_cols = [str(s["child_column_name"]) for s in subset]
                parent_cols = [str(s["parent_column_name"]) for s in subset]
                child_col_ids = [int(s["child_col_id"]) for s in subset]
                parent_col_ids = [int(s["parent_col_id"]) for s in subset]

                try:
                    res = _validate_composite_one(
                        con,
                        child_parquet,
                        child_cols,
                        parent_parquet,
                        parent_cols,
                        containment_threshold=containment_threshold,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning(
                        "phase4b_composite.validate_failed",
                        child_table=child_table,
                        parent_table=parent_table,
                        child_cols=child_cols,
                        parent_cols=parent_cols,
                        error=str(exc),
                    )
                    continue

                if res.containment_full < min_containment:
                    continue

                avg_sim = _avg_name_similarity(child_cols, parent_cols)
                confirmed.append(
                    CompositeFkCandidate(
                        child_table=child_table,
                        parent_table=parent_table,
                        child_columns=child_cols,
                        parent_columns=parent_cols,
                        containment=res.containment_full,
                        cardinality=res.cardinality,
                        name_similarity=round(avg_sim, 4),
                        child_table_id=child_table_id,
                        parent_table_id=parent_table_id,
                        child_col_ids=child_col_ids,
                        parent_col_ids=parent_col_ids,
                    )
                )
    finally:
        con.close()

    log.info(
        "phase4b_composite.discovered",
        confirmed=len(confirmed),
    )
    return confirmed


# ---------------------------------------------------------------------------
# Phase 4b orchestrator
# ---------------------------------------------------------------------------


def run_phase_4b_composite(
    engine: "Engine",
    config: "AppConfig",
) -> int:
    """Orchestrate Phase 4b: discover composite FKs and persist them.

    Returns the number of composite FKs persisted to
    ``composite_relationships``.

    Reads tunables from ``config.relationships`` when present:
      * ``containment_threshold`` (default 0.95)
      * ``composite_max_arity`` (default 3)
      * ``composite_max_proposals_per_pair`` (default 10)

    The persistence step uses a single ``txn(engine)`` for all rows; on
    failure the orchestrator logs and returns 0 — composite FKs are an
    additive surface, never required for downstream phases.
    """
    from discovery.results_db import (
        CompositeRelationship as CompositeRelationshipDAO,
        txn,
    )

    rel_cfg = getattr(config, "relationships", None)
    min_containment: float = float(
        getattr(rel_cfg, "containment_threshold", 0.95)
    )
    max_arity: int = int(
        getattr(rel_cfg, "composite_max_arity", 3)
    )
    max_proposals_per_pair: int = int(
        getattr(rel_cfg, "composite_max_proposals_per_pair", 10)
    )

    candidates = find_composite_fks(
        engine,
        config,
        max_arity=max_arity,
        min_containment=min_containment,
        max_proposals_per_pair=max_proposals_per_pair,
    )

    if not candidates:
        log.info("phase4b_composite.nothing_to_persist")
        return 0

    written = 0
    try:
        with txn(engine) as conn:
            dao = CompositeRelationshipDAO(conn)
            for c in candidates:
                if c.child_table_id is None or c.parent_table_id is None:
                    continue
                dao.upsert(
                    {
                        "child_table_id": c.child_table_id,
                        "parent_table_id": c.parent_table_id,
                        "child_columns": c.child_columns,
                        "parent_columns": c.parent_columns,
                        "containment_full": c.containment,
                        "cardinality": c.cardinality,
                        "name_similarity": c.name_similarity,
                    }
                )
                written += 1
    except Exception as exc:
        log.error("phase4b_composite.persist_failed", error=str(exc))
        return 0

    log.info(
        "phase4b_composite.complete",
        confirmed=len(candidates),
        persisted=written,
    )
    return written


# ---------------------------------------------------------------------------
# Internal helpers exported for tests
# ---------------------------------------------------------------------------

__all__ = [
    "CompositeFkCandidate",
    "CompositeValidationResult",
    "find_composite_fks",
    "run_phase_4b_composite",
    "_enumerate_subsets",
    "_validate_composite_one",
    "_should_propose_composite",
    "_avg_name_similarity",
    "_pair_name_similarity_floor",
    "_classify",
]
