"""
Inheritance / is-a relationship annotator.

Some FKs really mean ``child IS-A parent`` -- a vendor *is a* business
entity, a store *is a* business entity.  In schemas modelled this way
both columns are PKs of their tables, the child PK is contained in the
parent PK with full containment, the child has fewer rows than the
parent (a strict subset), and the column names match.

This module post-processes ``relationships`` rows produced by Phase 5.
For each row that satisfies the predicate
::

    child.is_pk AND parent.is_pk
    AND containment_full = 1.0
    AND child.distinct_count <= parent.distinct_count
    AND name_similarity(child_col, parent_col) >= min_name_sim

we MERGE ``{"is_a_inheritance": true}`` into ``relationships.evidence``.
The merge is non-destructive -- existing ``evidence`` keys (confidence
sub-scores, scoring breakdown, etc. populated by Phase 5) are preserved.

Implementation note
-------------------
The annotator updates the ``evidence`` JSONB via the Postgres concat
operator ``||`` which performs a shallow merge:

    evidence = COALESCE(evidence, '{}'::jsonb) || '{"is_a_inheritance": true}'::jsonb

This preserves keys present on either side; the right operand wins on
conflicts (which is fine -- we own the ``is_a_inheritance`` key).

Exports
-------
annotate_inheritance       -- pure-ish: returns the updated row count
run_phase_inheritance      -- orchestrator (no-op when empty)
"""
from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from discovery.config import AppConfig

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


_PLURAL_RE_IES = re.compile(r"ies$", re.IGNORECASE)
_PLURAL_RE_ES = re.compile(r"(ses|xes|ches|shes)$", re.IGNORECASE)
_PLURAL_RE_S = re.compile(r"s$", re.IGNORECASE)


def _normalize(name: str) -> str:
    """Lowercase + strip naive plural suffix."""
    n = name.lower().strip()
    if not n:
        return ""
    if _PLURAL_RE_IES.search(n) and len(n) > 3:
        return n[:-3] + "y"
    if _PLURAL_RE_ES.search(n) and len(n) > 3:
        return n[:-2]
    if _PLURAL_RE_S.search(n) and not n.endswith("ss") and len(n) > 1:
        return n[:-1]
    return n


def _name_similarity(a: str, b: str) -> float:
    """difflib SequenceMatcher with plural normalization on both sides."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(
        None, _normalize(a), _normalize(b)
    ).ratio()


# ---------------------------------------------------------------------------
# Annotator
# ---------------------------------------------------------------------------


_SELECT_CANDIDATES_SQL = text(
    """
    SELECT
        r.rel_id,
        r.containment_full,
        c.column_name AS child_col_name,
        c.is_pk      AS child_is_pk,
        c.distinct_count AS child_distinct,
        ct.table_name AS child_table_name,
        p.column_name AS parent_col_name,
        p.is_pk      AS parent_is_pk,
        p.distinct_count AS parent_distinct,
        pt.table_name AS parent_table_name
    FROM discovery.relationships r
    JOIN discovery.col_inventory c ON c.column_id = r.child_col_id
    JOIN discovery.tbl_inventory ct ON ct.table_id = c.table_id
    JOIN discovery.col_inventory p ON p.column_id = r.parent_col_id
    JOIN discovery.tbl_inventory pt ON pt.table_id = p.table_id
    WHERE  c.is_pk = true
      AND  p.is_pk = true
      AND  r.containment_full IS NOT NULL
      AND  r.containment_full >= 0.9999
    """
)


_UPDATE_EVIDENCE_SQL = text(
    """
    UPDATE discovery.relationships
    SET    evidence = COALESCE(evidence, '{}'::jsonb)
                      || jsonb_build_object('is_a_inheritance', true)
    WHERE  rel_id = :rel_id
    """
)


def annotate_inheritance(
    engine: "Engine",
    *,
    min_name_sim: float = 0.95,
) -> int:
    """Tag every qualifying ``relationships`` row with ``is_a_inheritance``.

    Returns the count of rows updated.  Idempotent -- re-running merges the
    same key, which is a no-op on an already-tagged row.
    """
    with engine.connect() as conn:
        rows = conn.execute(_SELECT_CANDIDATES_SQL).mappings().all()

    qualifying: list[int] = []
    for row in rows:
        # Containment must be effectively 1.0 (the SQL gate uses >= 0.9999
        # because REAL containment is rounded to 4 decimal places).
        cf = float(row["containment_full"] or 0.0)
        if cf < 0.9999:
            continue
        cd = int(row.get("child_distinct") or 0)
        pd = int(row.get("parent_distinct") or 0)
        if cd > pd and pd > 0:
            # The child shouldn't be larger than the parent under is-a.
            continue
        sim = _name_similarity(
            str(row["child_col_name"] or ""),
            str(row["parent_col_name"] or ""),
        )
        if sim < min_name_sim:
            continue
        qualifying.append(int(row["rel_id"]))

    if not qualifying:
        log.info("inheritance.no_qualifying_rows")
        return 0

    updated = 0
    with engine.begin() as conn:
        for rel_id in qualifying:
            conn.execute(_UPDATE_EVIDENCE_SQL, {"rel_id": rel_id})
            updated += 1

    log.info(
        "inheritance.tagged",
        candidates=len(rows),
        qualifying=len(qualifying),
        updated=updated,
    )
    return updated


def run_phase_inheritance(
    engine: "Engine",
    config: "AppConfig",
) -> int:
    """Phase orchestrator -- discover and tag inheritance relationships.

    Reads ``config.relationships.inheritance_min_name_sim`` (default 0.95).
    Runs after Phase 5 so the per-pair containment is already known.
    """
    rel_cfg = getattr(config, "relationships", None)
    enabled: bool = bool(getattr(rel_cfg, "inheritance_annotator_enabled", True))
    if not enabled:
        log.info("inheritance.disabled_by_config")
        return 0
    min_name_sim: float = float(
        getattr(rel_cfg, "inheritance_min_name_sim", 0.95)
    )
    return annotate_inheritance(engine, min_name_sim=min_name_sim)


__all__ = [
    "_name_similarity",
    "_normalize",
    "annotate_inheritance",
    "run_phase_inheritance",
]
