"""
Phase 5 — FK validation via DuckDB on local Parquet.

Owns BOTH the pure validators (validate_one, validate_group) and the Phase 5
orchestrator (run_phase_5).

Pure helpers have no SQLAlchemy / config / run_log imports.  The orchestrator
imports those at function-scope.

Each worker process opens its own DuckDB connection (DuckDB connections are
not safely shareable across processes).

Performance design (Phase 5 perf rework)
----------------------------------------
* Anti-join is expressed as ``LEFT JOIN ... WHERE p IS NULL`` — DuckDB's
  optimiser flattens this into a hash anti-join, while a correlated
  ``NOT IN (SELECT v FROM p)`` is sometimes evaluated per row, especially
  on nullable columns.  See B1.
* Per-worker DuckDB connection sets ``enable_object_cache=true`` so Parquet
  footers are cached across queries on the same file.  See B3.
* Physical-type lookup is hoisted: when ``col_inventory.physical_type`` is
  populated by Phase 1 (C3), validate.py reads it directly via the task
  tuple.  Otherwise it falls back to a DESCRIBE call cached per
  (parquet_path, column) within a worker.  See B2.
* Parent distinct values are materialised once per parent column into a
  worker-local DuckDB temp table; all child candidates against the same
  parent reuse it.  See B4 / ``validate_group``.

Exports
-------
ValidationResult        output dataclass
validate_one            validate a single candidate pair (pure)
validate_group          validate a group of children sharing one parent (pure)
exact_containment_topk  exact containment of one child vs K parent candidates
run_phase_5             coordinator
"""
from __future__ import annotations

import functools
import multiprocessing
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional

import structlog

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from discovery.config import AppConfig

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Full-data validation result for one candidate FK pair."""

    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    child_parquet: str
    parent_parquet: str
    child_distinct: int
    parent_distinct: int
    orphan_count: int
    containment_full: float
    cardinality: str
    query_duration_ms: int

    source_stage: Optional[str] = None
    sketch_similarity: Optional[float] = None


# ---------------------------------------------------------------------------
# Physical-type / family handling
# ---------------------------------------------------------------------------


_PHYS_TYPE_FAMILY = {
    # Map DuckDB physical types onto compatibility families. Only columns
    # whose Parquet physical type lands in the same family are compared
    # directly — no implicit CAST is performed during validation.
    "TINYINT": "INTEGER", "SMALLINT": "INTEGER", "INTEGER": "INTEGER",
    "BIGINT": "INTEGER", "HUGEINT": "INTEGER",
    "UTINYINT": "INTEGER", "USMALLINT": "INTEGER", "UINTEGER": "INTEGER",
    "UBIGINT": "INTEGER",
    "VARCHAR": "STRING", "TEXT": "STRING", "BLOB": "STRING", "UUID": "STRING",
    "DATE": "DATE", "TIMESTAMP": "TIMESTAMP", "TIMESTAMPTZ": "TIMESTAMP",
    "TIME": "TIME",
    "FLOAT": "FLOAT", "DOUBLE": "FLOAT", "DECIMAL": "FLOAT",
    "BOOLEAN": "BOOLEAN",
}


def _family_from_raw(raw_type: str | None) -> Optional[str]:
    """Map a raw DuckDB physical-type string (e.g. 'BIGINT', 'DECIMAL(18,2)')
    onto a compatibility family. Returns None for unrecognised / NULL inputs.
    """
    if not raw_type:
        return None
    base = str(raw_type).upper().split("(", 1)[0].strip()
    return _PHYS_TYPE_FAMILY.get(base)


# Per-worker DESCRIBE cache. Workers are separate processes, so a module-level
# lru_cache is safe and effectively isolated. Key: (parquet_path, column).
@functools.lru_cache(maxsize=512)
def _describe_physical_type(parquet_path: str, column: str) -> Optional[str]:
    """Run a DESCRIBE on the worker's connection to discover the raw physical
    type of one parquet column. Cached per (path, column) within the worker.
    """
    if _worker_con is None:
        return None
    rows = _worker_con.execute(  # type: ignore[union-attr]
        f'DESCRIBE SELECT "{column}" FROM read_parquet(\'{parquet_path}\') LIMIT 0'
    ).fetchall()
    if not rows:
        return None
    return str(rows[0][1]).upper()


def _physical_family(
    con: object,
    parquet_path: str,
    column: str,
    inventory_raw_type: str | None = None,
) -> Optional[str]:
    """Return the compatibility family for a parquet column, or None on miss.

    Preference order (B2):
      1. ``inventory_raw_type`` — the raw DuckDB type stored in
         col_inventory.physical_type (populated by Phase 1 / C3).
         Assumed to be the *raw* type (e.g. 'BIGINT', 'VARCHAR'); the family
         mapping is applied here so that storage stays at the DESCRIBE level.
      2. Worker-cached DESCRIBE call (lru_cache, see ``_describe_physical_type``).

    Note ``con`` is unused when (1) hits, and is taken from
    ``_worker_con`` inside ``_describe_physical_type`` (DuckDB connections
    are not hashable so we cannot key the cache on them).  When called from
    ``validate_one`` outside a worker (e.g. unit tests), the lru_cache miss
    path uses ``_worker_con`` if set; tests that pass a non-worker ``con``
    rely on path (1) or an explicit DESCRIBE — see fallback below.
    """
    fam = _family_from_raw(inventory_raw_type)
    if fam is not None:
        return fam

    # Worker fast path: cached DESCRIBE on the worker connection.
    if _worker_con is not None:
        return _family_from_raw(_describe_physical_type(parquet_path, column))

    # Test / non-worker path: use the explicitly supplied connection.
    rows = con.execute(  # type: ignore[union-attr]
        f'DESCRIBE SELECT "{column}" FROM read_parquet(\'{parquet_path}\') LIMIT 0'
    ).fetchall()
    if not rows:
        return None
    return _family_from_raw(str(rows[0][1]).upper())


# ---------------------------------------------------------------------------
# Cardinality bucket (shared by validate_one / validate_group)
# ---------------------------------------------------------------------------


def _classify(
    cd: int, pd: int, orphans: int, containment_threshold: float
) -> tuple[float, str]:
    """Compute (containment_full, cardinality) from raw counts."""
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


# ---------------------------------------------------------------------------
# Core validation (pure)
# ---------------------------------------------------------------------------


def validate_one(
    con: object,
    child_parquet: Path,
    child_col: str,
    parent_parquet: Path,
    parent_col: str,
    containment_threshold: float = 0.95,
    *,
    child_phys_type: str | None = None,
    parent_phys_type: str | None = None,
) -> ValidationResult:
    """Validate one FK candidate using a single DuckDB query.

    Pre-flight check: the actual Parquet physical types of child and parent
    columns must match (no implicit casts). If they don't, the candidate is
    flagged TYPE_MISMATCH and no comparison is run.

    Notes
    -----
    The orphan computation uses ``LEFT JOIN ... WHERE p.v IS NULL`` (B1 /
    QW-2) — DuckDB's hash anti-join is materially faster than the original
    correlated ``NOT IN`` subquery, especially on nullable columns.
    Wrapped in CTEs ``c`` and ``p`` so DuckDB sees a single anti-join plan.
    """
    child_path = str(child_parquet)
    parent_path = str(parent_parquet)

    t0 = time.monotonic()

    child_fam = _physical_family(con, child_path, child_col, child_phys_type)
    parent_fam = _physical_family(con, parent_path, parent_col, parent_phys_type)
    if child_fam is None or parent_fam is None or child_fam != parent_fam:
        return ValidationResult(
            child_table=Path(child_parquet).stem, child_column=child_col,
            parent_table=Path(parent_parquet).stem, parent_column=parent_col,
            child_parquet=child_path, parent_parquet=parent_path,
            child_distinct=0, parent_distinct=0, orphan_count=0,
            containment_full=0.0, cardinality="TYPE_MISMATCH",
            query_duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # B1: LEFT-anti-join formulation. Single pass over (c, p) computes
    # child_distinct, parent_distinct, and orphan_count. DuckDB's optimiser
    # flattens LEFT JOIN ... IS NULL into a hash anti-join.
    result = con.execute(  # type: ignore[union-attr]
        f"""
        WITH
          c AS (
            SELECT DISTINCT "{child_col}" AS v
            FROM   read_parquet('{child_path}')
            WHERE  "{child_col}" IS NOT NULL
          ),
          p AS (
            SELECT DISTINCT "{parent_col}" AS v
            FROM   read_parquet('{parent_path}')
            WHERE  "{parent_col}" IS NOT NULL
          )
        SELECT
          (SELECT COUNT(*) FROM c)                         AS child_distinct,
          (SELECT COUNT(*) FROM p)                         AS parent_distinct,
          (SELECT COUNT(*) FROM c LEFT JOIN p ON c.v = p.v
                          WHERE p.v IS NULL)               AS orphan_count
        """
    ).fetchone()

    query_duration_ms = int((time.monotonic() - t0) * 1000)

    if result is None:
        cd = pd = orphans = 0
    else:
        cd, pd, orphans = int(result[0]), int(result[1]), int(result[2])

    containment_full, cardinality = _classify(cd, pd, orphans, containment_threshold)

    return ValidationResult(
        child_table=Path(child_parquet).stem,
        child_column=child_col,
        parent_table=Path(parent_parquet).stem,
        parent_column=parent_col,
        child_parquet=child_path,
        parent_parquet=parent_path,
        child_distinct=cd,
        parent_distinct=pd,
        orphan_count=orphans,
        containment_full=containment_full,
        cardinality=cardinality,
        query_duration_ms=query_duration_ms,
    )


# ---------------------------------------------------------------------------
# Parent-set materialisation (B4 / M-1)
# ---------------------------------------------------------------------------


def validate_group(
    con: object,
    parent_parquet: Path,
    parent_col: str,
    children: Iterable[tuple[Path, str, str | None, str | None]],
    containment_threshold: float = 0.95,
) -> list[ValidationResult]:
    """Validate a batch of children that all share the same parent column.

    Materialises the parent's distinct set into a DuckDB temp table once,
    then runs each child's anti-join against that temp table.  This trades
    the per-candidate parent re-read (the dominant cost at fan-in 10–50)
    for a one-time materialisation per group.

    Parameters
    ----------
    con
        DuckDB connection (worker-local in production).
    parent_parquet, parent_col
        Parent parquet file and column.
    children
        Iterable of ``(child_parquet, child_col, child_phys_type, parent_phys_type)``.
        ``parent_phys_type`` is repeated per row only because the worker
        receives flattened task tuples; values are expected to agree.
    containment_threshold
        Threshold used by :func:`_classify` for PARTIAL classification.

    Returns
    -------
    list[ValidationResult]
        One result per input child, in input order. TYPE_MISMATCH children
        get a synthetic result and never touch the temp table.
    """
    parent_path = str(parent_parquet)
    children_list = list(children)
    results: list[ValidationResult | None] = [None] * len(children_list)

    # First pass: figure out which children pass the type-mismatch gate.
    # We resolve parent_fam once below, after scanning, so we can choose
    # whether to materialise the temp table.
    parent_phys_hint: str | None = None
    for _, _, _, ph in children_list:
        if ph is not None:
            parent_phys_hint = ph
            break

    parent_fam = _physical_family(con, parent_path, parent_col, parent_phys_hint)

    # Determine per-child families and short-circuit type mismatches.
    child_fams: list[Optional[str]] = []
    needs_query: list[int] = []
    for idx, (child_path, child_col, child_phys, _parent_phys) in enumerate(children_list):
        cfam = _physical_family(con, str(child_path), child_col, child_phys)
        child_fams.append(cfam)
        if parent_fam is None or cfam is None or parent_fam != cfam:
            t0 = time.monotonic()
            results[idx] = ValidationResult(
                child_table=Path(child_path).stem, child_column=child_col,
                parent_table=Path(parent_parquet).stem, parent_column=parent_col,
                child_parquet=str(child_path), parent_parquet=parent_path,
                child_distinct=0, parent_distinct=0, orphan_count=0,
                containment_full=0.0, cardinality="TYPE_MISMATCH",
                query_duration_ms=int((time.monotonic() - t0) * 1000),
            )
        else:
            needs_query.append(idx)

    if not needs_query:
        return [r for r in results if r is not None]  # all type-mismatched

    # Materialise the parent set ONCE for this group (B4).
    con.execute("DROP TABLE IF EXISTS parent_set")  # type: ignore[union-attr]
    con.execute(  # type: ignore[union-attr]
        f"""
        CREATE TEMP TABLE parent_set AS
        SELECT DISTINCT "{parent_col}" AS col
        FROM   read_parquet('{parent_path}')
        WHERE  "{parent_col}" IS NOT NULL
        """
    )
    parent_distinct = int(
        con.execute("SELECT COUNT(*) FROM parent_set").fetchone()[0]  # type: ignore[union-attr]
    )

    try:
        for idx in needs_query:
            child_path, child_col, _cphys, _pphys = children_list[idx]
            t0 = time.monotonic()
            row = con.execute(  # type: ignore[union-attr]
                f"""
                WITH c AS (
                    SELECT DISTINCT "{child_col}" AS v
                    FROM   read_parquet('{str(child_path)}')
                    WHERE  "{child_col}" IS NOT NULL
                )
                SELECT
                  (SELECT COUNT(*) FROM c)                                AS child_distinct,
                  (SELECT COUNT(*) FROM parent_set)                       AS parent_distinct,
                  (SELECT COUNT(*) FROM c LEFT JOIN parent_set p
                                  ON c.v = p.col
                                  WHERE p.col IS NULL)                    AS orphan_count
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
            results[idx] = ValidationResult(
                child_table=Path(child_path).stem,
                child_column=child_col,
                parent_table=Path(parent_parquet).stem,
                parent_column=parent_col,
                child_parquet=str(child_path),
                parent_parquet=parent_path,
                child_distinct=cd,
                parent_distinct=pd,
                orphan_count=orphans,
                containment_full=containment_full,
                cardinality=cardinality,
                query_duration_ms=query_duration_ms,
            )
    finally:
        # Drop the per-group temp table to bound worker memory.
        con.execute("DROP TABLE IF EXISTS parent_set")  # type: ignore[union-attr]

    # mypy: every slot is set at this point.
    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Tier 3 #11 — exact containment top-K (child-distinct materialisation)
# ---------------------------------------------------------------------------


def exact_containment_topk(
    con: object,
    child_parquet: Path,
    child_col: str,
    parent_candidates: list[tuple[Path, str]],
    top_k: int = 32,
    *,
    child_phys_type: str | None = None,
    parent_phys_types: list[str | None] | None = None,
) -> list[tuple[int, float]]:
    """Exact containment of one child column in K parent candidates.

    Inverse of :func:`validate_group`: instead of one parent vs many children,
    this materialises the *child*'s distinct set once and runs a hash
    anti-join against each parent candidate. Used as an exact, configurable
    alternative to MinHash Jaccard estimation in Phase 4b.

    Parameters
    ----------
    con
        DuckDB connection.
    child_parquet, child_col
        Child parquet path and column.
    parent_candidates
        List of ``(parquet_path, column_name)`` tuples — one per candidate
        parent column to score.
    top_k
        Number of top results to return. If fewer parents are supplied than
        ``top_k`` they all come back.
    child_phys_type
        Optional hint for the child's raw physical type, avoids a DESCRIBE.
    parent_phys_types
        Optional per-parent hints in the same order as ``parent_candidates``.

    Returns
    -------
    list[tuple[int, float]]
        ``(parent_index, containment)`` pairs sorted by containment DESC,
        truncated to ``top_k``.  ``parent_index`` is the position in the
        input list. Type-mismatched parents contribute containment=0 and
        rank last; the per-parent query is skipped to save work.

    Notes
    -----
    Implementation strategy::

        1. Materialise child distinct values into a per-call TEMP TABLE
           (`_child_distinct_topk`) — chosen to never collide with the
           ``parent_set`` temp table that :func:`validate_group` uses.
        2. For each parent candidate, run::

               SELECT COUNT(*) FROM _child_distinct_topk c
                  LEFT JOIN read_parquet(parent_path) p
                  ON c.v = p.<col>
               WHERE p.<col> IS NULL

           and divide that orphan count by ``child_distinct``.
        3. ``containment = 1 - orphans / child_distinct`` — clamped to 0.0
           when ``child_distinct == 0`` to mirror :func:`_classify`.
        4. Drop the temp table in a ``finally`` so an interrupted scan
           never leaves stale state on the connection.

    The win versus calling :func:`validate_one` in a loop is a single read
    of the child column.  Real-world wall-time savings are typically
    1.5–3× — parent-side reads still dominate per-candidate work.
    """
    child_path = str(child_parquet)
    n_parents = len(parent_candidates)

    log.info(
        "exact_containment_topk_start",
        child_table=Path(child_parquet).stem,
        child_column=child_col,
        n_candidates=n_parents,
        top_k=top_k,
    )

    if n_parents == 0:
        log.info("exact_containment_topk_done", elapsed_ms=0, returned=0)
        return []

    t0 = time.monotonic()

    # Resolve the child's family ONCE so type-mismatch checks are cheap.
    child_fam = _physical_family(con, child_path, child_col, child_phys_type)

    # Materialise the child's distinct set into a per-call temp table.
    # Name avoids any collision with ``parent_set`` (used by validate_group).
    TEMP_TABLE = "_child_distinct_topk"
    con.execute(f"DROP TABLE IF EXISTS {TEMP_TABLE}")  # type: ignore[union-attr]
    con.execute(  # type: ignore[union-attr]
        f"""
        CREATE TEMP TABLE {TEMP_TABLE} AS
        SELECT DISTINCT "{child_col}" AS v
        FROM   read_parquet('{child_path}')
        WHERE  "{child_col}" IS NOT NULL
        """
    )
    child_distinct = int(
        con.execute(f"SELECT COUNT(*) FROM {TEMP_TABLE}").fetchone()[0]  # type: ignore[union-attr]
    )

    results: list[tuple[int, float]] = []
    try:
        # Short-circuit: empty child set → containment is 0.0 for every parent
        # (matches :func:`_classify`'s ``cd == 0 -> 0.0`` convention) and
        # there is no work to do.
        if child_distinct == 0:
            results = [(i, 0.0) for i in range(n_parents)]
        else:
            phys_hints = parent_phys_types or [None] * n_parents
            for idx, (parent_parquet, parent_col) in enumerate(parent_candidates):
                phys_hint = phys_hints[idx] if idx < len(phys_hints) else None

                # Type-mismatch guard: skip the query, contribute 0.0.
                parent_fam = _physical_family(
                    con, str(parent_parquet), parent_col, phys_hint
                )
                if (
                    child_fam is None
                    or parent_fam is None
                    or child_fam != parent_fam
                ):
                    results.append((idx, 0.0))
                    if (idx + 1) % 10 == 0:
                        log.info(
                            "exact_containment_topk_progress",
                            processed=idx + 1,
                            total=n_parents,
                        )
                    continue

                row = con.execute(  # type: ignore[union-attr]
                    f"""
                    SELECT COUNT(*)
                    FROM   {TEMP_TABLE} c
                    LEFT JOIN read_parquet('{str(parent_parquet)}') p
                           ON c.v = p."{parent_col}"
                    WHERE  p."{parent_col}" IS NULL
                    """
                ).fetchone()
                orphans = int(row[0]) if row is not None else 0
                containment = 1.0 - orphans / child_distinct
                # Numerical clamp — orphans should never exceed child_distinct
                # but defend against rounding / divergent COUNT semantics.
                if containment < 0.0:
                    containment = 0.0
                results.append((idx, round(containment, 4)))

                if (idx + 1) % 10 == 0:
                    log.info(
                        "exact_containment_topk_progress",
                        processed=idx + 1,
                        total=n_parents,
                    )
    finally:
        con.execute(f"DROP TABLE IF EXISTS {TEMP_TABLE}")  # type: ignore[union-attr]

    # Sort by containment DESC; stable sort keeps input order on ties.
    results.sort(key=lambda pair: pair[1], reverse=True)
    truncated = results[:top_k]

    log.info(
        "exact_containment_topk_done",
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        n_candidates=n_parents,
        returned=len(truncated),
        child_distinct=child_distinct,
    )
    return truncated


# ---------------------------------------------------------------------------
# Per-worker DuckDB connection
# ---------------------------------------------------------------------------

_worker_con: Any = None
_worker_settings: dict = {}


def _worker_init(settings: dict) -> None:
    """Open a DuckDB connection once per worker process."""
    global _worker_con, _worker_settings
    import duckdb  # noqa: PLC0415

    _worker_settings = settings
    _worker_con = duckdb.connect()
    _worker_con.execute(f"SET memory_limit = '{settings['memory_limit_per_worker']}'")
    if settings.get("temp_directory"):
        _worker_con.execute(f"SET temp_directory = '{settings['temp_directory']}'")
    # B3 / QW-3: cache Parquet metadata (footers / column stats) across queries
    # on the same file. With B4 the parent path is hit once per group, but
    # the child path is hit once per candidate; cached footers shave 5–10%
    # off wall time on shared parent groups too.
    _worker_con.execute("SET enable_object_cache=true")
    # Reset DESCRIBE cache for this worker. Workers are forked but in case
    # of the spawn start method we still want a clean state.
    _describe_physical_type.cache_clear()


def _validate_parent_group_task(arg: tuple) -> list[dict]:
    """Worker entry point for one parent group.

    Input shape::

        (
            (parent_parquet, parent_col, parent_phys_type),
            [
                (
                    candidate_id, child_col_id, parent_col_id,
                    child_parquet, child_col, child_phys_type,
                    source_stage, sketch_similarity,
                ),
                ...
            ],
            containment_threshold,
        )

    Returns one dict per child (in input order) with the same shape that
    the orchestrator's persistence loop expects, or ``None`` for failed
    children.
    """
    (parent_parquet, parent_col, parent_phys_type), children, containment_threshold = arg
    out: list[dict] = []

    children_input = [
        (Path(c[3]), c[4], c[5], parent_phys_type) for c in children
    ]

    try:
        results = validate_group(
            con=_worker_con,
            parent_parquet=Path(parent_parquet),
            parent_col=parent_col,
            children=children_input,
            containment_threshold=containment_threshold,
        )
    except Exception as exc:
        log.error(
            "validate_group_failed",
            parent_parquet=parent_parquet,
            parent_col=parent_col,
            error=str(exc),
            exc_info=True,
        )
        # Per-child None failure rows so the orchestrator can still mark
        # each candidate failed individually.
        return [
            {
                "candidate_id": c[0],
                "child_col_id": c[1],
                "parent_col_id": c[2],
                "failed": True,
                "error": str(exc),
            }
            for c in children
        ]

    for child_args, result in zip(children, results):
        candidate_id, child_col_id, parent_col_id, _cp, _cc, _cphys, source_stage, sketch_similarity = child_args
        result.source_stage = source_stage
        result.sketch_similarity = sketch_similarity
        out.append(
            {
                "candidate_id": candidate_id,
                "child_col_id": child_col_id,
                "parent_col_id": parent_col_id,
                "containment_full": result.containment_full,
                "cardinality": result.cardinality,
                "child_distinct": result.child_distinct,
                "parent_distinct": result.parent_distinct,
                "orphan_count": result.orphan_count,
                "query_duration_ms": result.query_duration_ms,
                "source_stage": source_stage,
                "sketch_similarity": sketch_similarity,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_and_divide_memory(raw: str, n: int) -> str:
    """Parse a DuckDB memory string and divide by n workers.

    Accepts KB|MB|GB|TB suffixes (case-insensitive) or a plain integer (bytes).
    Falls back to '4GB' on parse error.
    """
    try:
        s = str(raw).strip().upper()
        # Try integer bytes
        if s.isdigit():
            bytes_per = int(s) // max(n, 1)
            return f"{bytes_per}"
        units = {"KB": 1, "MB": 2, "GB": 3, "TB": 4}
        for unit, _ in sorted(units.items(), key=lambda kv: -len(kv[0])):
            if s.endswith(unit):
                value = float(s[: -len(unit)]) / max(n, 1)
                return f"{value:.0f}{unit}"
    except (ValueError, AttributeError, TypeError):
        pass
    return "4GB"


# ---------------------------------------------------------------------------
# Phase 5 entry point
# ---------------------------------------------------------------------------


def run_phase_5(
    engine: "Engine",
    config: "AppConfig",
    parquet_dir: Path | None = None,
    limit: int | None = None,
) -> None:
    """
    Orchestrate Phase 5: validate pending FK candidates via DuckDB.

    Reads from fk_candidates where the canonical run_log ledger does not
    show a 'succeeded' row for ``(phase='validate', scope_type='candidate',
    scope_id=candidate_id)``. This subsumes both relationships rows that
    were written and outcomes that did not produce a relationships row
    (NO_RELATIONSHIP / TYPE_MISMATCH / PARTIAL below threshold) — see E3.

    Writes to relationships for pairs meeting containment_threshold.

    Parameters
    ----------
    parquet_dir:
        Reserved for future use.  Today the parquet path is read directly
        from tbl_inventory.parquet_path.
    limit:
        If set, validate at most this many candidates.
    """
    from sqlalchemy import and_, exists, select

    from discovery.results_db import (
        Relationship,
        col_inventory_t,
        fk_candidates_t,
        run_log_t,
        tbl_inventory_t,
        txn,
    )
    from discovery.run_log import RunLog

    # function-scope import: scoring lives in its own module (C1).
    # Import at function scope keeps the module-level ``from discovery.validate
    # import ...`` smoke test independent of C1 landing.
    from discovery.scoring import compute_confidence

    run_log = RunLog(engine)

    rel_cfg = getattr(config, "relationships", None)
    containment_threshold: float = getattr(rel_cfg, "containment_threshold", 0.95)
    # F1.2 / A5: filter ``fk_candidates.tier='primary'`` so Phase 5 skips
    # ``advisory_lowconf`` candidates.  Defaults to True; toggle off via
    # ``config.relationships.validate_only_primary_tier`` if needed.
    # ``getattr`` with default makes the field optional on RelationshipsConfig
    # so the fix lands without requiring the F4 schema declaration.
    validate_only_primary: bool = bool(
        getattr(rel_cfg, "validate_only_primary_tier", True)
    )

    child_col = col_inventory_t.alias("child_col")
    parent_col = col_inventory_t.alias("parent_col")
    child_tbl = tbl_inventory_t.alias("child_tbl")
    parent_tbl = tbl_inventory_t.alias("parent_tbl")

    # B2: physical_type is owned by Phase 1 (C3); guard against the column
    # not yet existing on the col_inventory_t SQLAlchemy Table.
    has_phys_type = "physical_type" in col_inventory_t.c.keys()
    # F1.2: tier column may not exist in older schemas; guard like has_phys_type.
    has_tier = "tier" in fk_candidates_t.c.keys()

    # E3: skip candidates that have ANY succeeded run_log row.
    # run_log is the canonical "did this work succeed once" ledger.
    # NOT EXISTS subsumes both PARTIAL/NO_RELATIONSHIP/TYPE_MISMATCH (no
    # relationships row written) and ONE_TO_ONE/MANY_TO_ONE (relationships
    # row written) cases.
    succeeded_subq = (
        select(run_log_t.c.log_id)
        .where(
            and_(
                run_log_t.c.phase == "validate",
                run_log_t.c.scope_type == "candidate",
                run_log_t.c.scope_id == fk_candidates_t.c.candidate_id,
                run_log_t.c.status == "succeeded",
            )
        )
        .correlate(fk_candidates_t)
    )

    select_cols = [
        fk_candidates_t.c.candidate_id,
        fk_candidates_t.c.child_col_id,
        fk_candidates_t.c.parent_col_id,
        fk_candidates_t.c.source_stage,
        fk_candidates_t.c.estimated_containment,
        fk_candidates_t.c.name_similarity,
        child_col.c.column_name.label("child_column_name"),
        child_tbl.c.parquet_path.label("child_parquet_path"),
        parent_col.c.column_name.label("parent_column_name"),
        parent_tbl.c.parquet_path.label("parent_parquet_path"),
        parent_col.c.is_pk.label("parent_is_pk"),
        parent_col.c.is_unique_indexed.label("parent_is_unique_indexed"),
    ]
    if has_phys_type:
        select_cols.append(child_col.c.physical_type.label("child_physical_type"))
        select_cols.append(parent_col.c.physical_type.label("parent_physical_type"))

    where_clauses = [
        ~exists(succeeded_subq),
        child_tbl.c.parquet_path.is_not(None),
        parent_tbl.c.parquet_path.is_not(None),
    ]
    if validate_only_primary and has_tier:
        # F1.2 / A5: skip advisory_lowconf candidates entirely.
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

    pending = list(rows)
    if limit is not None:
        pending = pending[:limit]
    log.info("phase5_pending", count=len(pending))
    if not pending:
        log.info("phase5_nothing_to_do")
        return

    storage_cfg = getattr(config, "storage", None)
    raw_limit: str = getattr(storage_cfg, "duckdb_memory_limit", "32GB")
    temp_dir: str = str(getattr(storage_cfg, "duckdb_temp_dir", "/tmp/duckdb_tmp"))

    orch_cfg = getattr(config, "orchestration", None)
    workers_cfg = getattr(orch_cfg, "workers", None)
    # F1.1: Read ``validate_workers`` (Python attr) — NOT ``validate`` (which
    # resolves to the inherited BaseModel.validate bound method).  See
    # config.WorkersConfig docstring; the YAML key remains "validate" via alias.
    num_workers: int = getattr(workers_cfg, "validate_workers", 8)

    mem_per_worker = _parse_and_divide_memory(raw_limit, num_workers)
    log.info(
        "phase5_pool",
        workers=num_workers,
        memory_per_worker=mem_per_worker,
        temp_dir=temp_dir,
    )

    settings = {
        "memory_limit_per_worker": mem_per_worker,
        "temp_directory": temp_dir,
    }

    # B4 / M-1 + M-2: group candidates by (parent_parquet, parent_col).
    # Each task is one parent group; the worker materialises the parent set
    # once and reuses it for all children in the group.
    groups: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    parent_phys_for_group: dict[tuple[str, str], str | None] = {}
    # Side metadata not needed inside the worker but needed for confidence
    # at persistence time. Keyed on candidate_id.
    side_meta: dict[int, dict[str, Any]] = {}
    for row in pending:
        key = (row["parent_parquet_path"], row["parent_column_name"])
        groups[key].append(
            (
                row["candidate_id"],
                row["child_col_id"],
                row["parent_col_id"],
                row["child_parquet_path"],
                row["child_column_name"],
                row.get("child_physical_type") if has_phys_type else None,
                row["source_stage"],
                row["estimated_containment"],
            )
        )
        if has_phys_type:
            parent_phys_for_group.setdefault(
                key, row.get("parent_physical_type")
            )
        side_meta[row["candidate_id"]] = {
            "name_similarity": float(row.get("name_similarity") or 0.0),
            "parent_is_pk": bool(row.get("parent_is_pk") or False),
            "parent_is_unique_indexed": bool(
                row.get("parent_is_unique_indexed") or False
            ),
        }

    tasks = [
        (
            (parent_path, parent_col_name, parent_phys_for_group.get((parent_path, parent_col_name))),
            children,
            containment_threshold,
        )
        for (parent_path, parent_col_name), children in groups.items()
    ]
    log.info(
        "phase5_grouped",
        groups=len(tasks),
        candidates=len(pending),
        avg_fanin=round(len(pending) / max(len(tasks), 1), 2),
    )

    with multiprocessing.Pool(
        processes=num_workers,
        initializer=_worker_init,
        initargs=(settings,),
    ) as pool:
        all_results_grouped = pool.map(_validate_parent_group_task, tasks)

    # Flatten worker output back to a single sequence in the same order as
    # the children list inside each group.
    flat_children: list[tuple] = [c for _, children, _ in tasks for c in children]
    flat_results: list[dict | None] = []
    for grp in all_results_grouped:
        flat_results.extend(grp)

    success = failed = written = 0

    # F1.3 / E3: ``run_log.succeed`` MUST run AFTER the relationships flush
    # commits.  Otherwise a crash between ``succeed`` and the next ``_flush``
    # leaves the run_log marking the candidate "succeeded" with no
    # corresponding relationships row, and the resume filter
    # (``~exists(succeeded_subq)``) hides the candidate forever.
    #
    # We track two parallel lists, both cleared together by ``_flush``:
    #   * ``pending_writes`` — relationship rows to upsert (only candidates
    #     above the containment threshold).
    #   * ``pending_succeeds`` — candidate IDs to mark succeeded.  Includes
    #     EVERY validated candidate (relationship-writing AND no-relationship
    #     cases like NO_RELATIONSHIP / TYPE_MISMATCH / sub-threshold PARTIAL)
    #     so they are not re-validated on resume.
    batch_size = 500
    pending_writes: list[tuple[int, int, int, dict[str, Any]]] = []
    pending_succeeds: list[int] = []

    def _flush() -> None:
        nonlocal written
        if not pending_writes and not pending_succeeds:
            return
        # Phase 1: write all queued relationship rows in one transaction.
        if pending_writes:
            with txn(engine) as conn:
                dao = Relationship(conn)
                for _candidate_id, child_id, parent_id, payload in pending_writes:
                    dao.upsert(
                        {
                            "child_col_id": child_id,
                            "parent_col_id": parent_id,
                            **payload,
                        }
                    )
            written += len(pending_writes)
        # Phase 2: relationships are durable — mark candidates succeeded.
        # Only reached if the txn above committed (or was a no-op).
        for cid in pending_succeeds:
            run_log.succeed("validate", "candidate", cid)
        pending_writes.clear()
        pending_succeeds.clear()

    for child_args, result in zip(flat_children, flat_results):
        candidate_id = child_args[0]
        child_col_id = child_args[1]
        parent_col_id = child_args[2]

        if result is None or result.get("failed"):
            failed += 1
            err = (result or {}).get("error", "validate_task returned None")
            run_log.fail(
                "validate", "candidate", candidate_id, str(err)
            )
            continue

        # F1.3: defer ``succeed`` until AFTER the next _flush commits.
        # Every successfully validated candidate (relationship-writing or not)
        # joins ``pending_succeeds``.
        success += 1
        pending_succeeds.append(candidate_id)

        if result["containment_full"] >= containment_threshold:
            evidence = {
                "orphan_count": result["orphan_count"],
                "child_distinct": result["child_distinct"],
                "parent_distinct": result["parent_distinct"],
                "query_duration_ms": result["query_duration_ms"],
                "source_stage": result["source_stage"],
                "sketch_similarity": result["sketch_similarity"],
            }
            meta = side_meta.get(candidate_id, {})
            confidence = compute_confidence(
                containment_full=float(result["containment_full"]),
                name_similarity=float(meta.get("name_similarity") or 0.0),
                parent_is_pk=bool(meta.get("parent_is_pk") or False),
                parent_is_unique_indexed=bool(
                    meta.get("parent_is_unique_indexed") or False
                ),
                child_distinct=int(result["child_distinct"]),
                parent_distinct=int(result["parent_distinct"]),
                sketch_jaccard=float(result.get("sketch_similarity") or 0.0),
            )
            payload = {
                "containment_full": result["containment_full"],
                "cardinality": result["cardinality"],
                "confidence": confidence,
                "evidence": evidence,
                "validated_locally": True,
                "validation_method": "local_duckdb_full",
            }
            pending_writes.append((candidate_id, child_col_id, parent_col_id, payload))

        # Flush when EITHER queue grows past the batch size.  Using both
        # ensures no-relationship candidates are still committed promptly.
        if len(pending_writes) >= batch_size or len(pending_succeeds) >= batch_size:
            try:
                _flush()
            except Exception as exc:
                log.error("phase5_batch_flush_failed", error=str(exc))
                # Drop both queues — neither relationships nor run_log are
                # written, so the candidates are eligible for resume and
                # will be re-validated.
                pending_writes.clear()
                pending_succeeds.clear()

    try:
        _flush()
    except Exception as exc:
        log.error("phase5_final_flush_failed", error=str(exc))
        pending_writes.clear()
        pending_succeeds.clear()

    log.info(
        "phase5_complete",
        success=success,
        failed=failed,
        relationships_written=written,
    )
