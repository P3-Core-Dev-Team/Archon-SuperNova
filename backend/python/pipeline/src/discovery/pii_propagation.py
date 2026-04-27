"""Subject-rooted PII propagation.

Tags every table reachable from a PII direct-identifier column on
``tbl_inventory.subject_kinds`` (JSONB array) and ``subject_link_distance``
(closure depth from the nearest root).

Algorithm
---------
1.  Find every column in ``pii_findings`` whose ``pii_type`` belongs to the
    direct-identifier class set (EMAIL, PHONE, SSN_*, IBAN, CC_NUMBER,
    PASSPORT_*, IDENTITY_BUNDLE, ...) AND whose score >= score_floor (default
    0.85).  Each finding marks its TABLE as a *root*.
2.  Reverse-BFS over ``relationships`` (confidence >= confidence_floor):
    for every edge ``child_col → parent_col``, if the parent's table is
    already tagged, the child's table inherits the parent's ``subject_kinds``
    union.  Distance is the shortest hop count from any root.
3.  Persist via UPDATE on ``tbl_inventory``.

No distance decay — under GDPR a depth-5 link is still personal data.
Idempotent: clears prior tags before each recompute.
"""
from __future__ import annotations

import json
from collections import deque
from typing import Optional

import structlog
from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import JSONB

from discovery.config import AppConfig
from discovery.results_db import (
    col_inventory_t,
    pii_findings_t,
    relationships_t,
    run_log_t,
    tbl_inventory_t,
)

log = structlog.get_logger(__name__)


# Direct-identifier PII classes — these tag a table as a "subject root".
# Quasi-identifiers (POSTAL_CODE, GEO_COORD, DOB) are intentionally excluded;
# they form roots only in combination (handled separately by the
# IDENTITY_BUNDLE detector at scan time).
DIRECT_IDENTIFIER_TYPES: frozenset[str] = frozenset({
    "EMAIL", "PHONE", "SSN_US", "SSN", "PASSPORT_GB", "PASSPORT_US",
    "PASSPORT", "IBAN", "CC_NUMBER", "PESEL_PL", "BSN_NL", "NIR_FR",
    "AADHAAR_IN", "MRN", "NPI_US", "MEDICARE_MBI", "PERSONNUMMER_SE",
    "TAX_ID_DE", "CODICE_FISCALE_IT", "IDENTITY_BUNDLE",
})


def run_phase_pii_propagation(
    engine,
    config: AppConfig,
    *,
    score_floor: float = 0.85,
    confidence_floor: float = 0.8,
) -> dict:
    """Compute subject-kind closure and persist on tbl_inventory.

    Parameters
    ----------
    engine
        SQLAlchemy engine for the results DB.
    config
        Pipeline config (unused today; reserved for future per-domain
        identifier sets).
    score_floor
        Only PII findings with ``score >= score_floor`` seed roots.
    confidence_floor
        Only relationships with ``confidence >= confidence_floor`` are
        traversed.  Lower-confidence edges live in a "shadow" set that
        can be surfaced separately (out of scope here).

    Returns
    -------
    dict with counts: ``{tables_tagged, roots_seeded, max_distance}``.
    """
    log.info("phase_pii_propagation.starting", score_floor=score_floor,
             confidence_floor=confidence_floor)

    with engine.begin() as conn:
        # 1. Clear prior tags (idempotent).
        conn.execute(
            tbl_inventory_t.update().values(
                subject_kinds=None, subject_link_distance=None
            )
        )

        # 2. Build the FK adjacency: edges go child_table -> parent_table
        #    in the data model, so reverse-BFS means: starting from a root
        #    (parent) table, walk inbound edges (children referencing it).
        adj_rows = conn.execute(
            select(
                relationships_t.c.child_col_id,
                relationships_t.c.parent_col_id,
                relationships_t.c.confidence,
            ).where(relationships_t.c.confidence >= confidence_floor)
        ).fetchall()

        col_to_table = dict(conn.execute(
            select(col_inventory_t.c.column_id, col_inventory_t.c.table_id)
        ).fetchall())

        # parent_table_id -> [child_table_id, ...]
        inbound: dict[int, set[int]] = {}
        for child_col_id, parent_col_id, _conf in adj_rows:
            ct = col_to_table.get(int(child_col_id))
            pt = col_to_table.get(int(parent_col_id))
            if ct is None or pt is None or ct == pt:
                continue
            inbound.setdefault(int(pt), set()).add(int(ct))

        # 3. Find roots: tables with at least one column-level finding
        #    in DIRECT_IDENTIFIER_TYPES at score >= floor.
        rows = conn.execute(
            select(
                pii_findings_t.c.column_id,
                pii_findings_t.c.table_id,
                pii_findings_t.c.pii_type,
                pii_findings_t.c.score,
            ).where(
                pii_findings_t.c.pii_type.in_(list(DIRECT_IDENTIFIER_TYPES))
            )
        ).fetchall()

        roots: dict[int, set[str]] = {}
        for col_id, tbl_id, pii_type, score in rows:
            if score is None or float(score) < score_floor:
                continue
            # Resolve table_id: prefer the explicit table_id (table-level
            # finding e.g. IDENTITY_BUNDLE), else look up via column.
            t = int(tbl_id) if tbl_id is not None else col_to_table.get(int(col_id)) if col_id is not None else None
            if t is None:
                continue
            roots.setdefault(t, set()).add(str(pii_type))

        # 4. Reverse-BFS from each root.  A table's subject_kinds is the
        #    UNION of all root-kinds reachable; distance is shortest path.
        labels: dict[int, set[str]] = {tid: set(kinds) for tid, kinds in roots.items()}
        distance: dict[int, int] = {tid: 0 for tid in roots}

        queue: deque[int] = deque(roots.keys())
        while queue:
            t = queue.popleft()
            t_labels = labels[t]
            t_dist = distance[t]
            for child_t in inbound.get(t, ()):
                # Propagate kinds; if any new kind got added OR distance shrunk,
                # re-queue the child so its descendants pick up the change.
                child_labels = labels.setdefault(child_t, set())
                before = len(child_labels)
                child_labels |= t_labels
                shorter = (child_t not in distance) or (t_dist + 1 < distance[child_t])
                if shorter:
                    distance[child_t] = t_dist + 1
                if shorter or len(child_labels) > before:
                    queue.append(child_t)

        # 5. Persist.  One UPDATE per table; small (≤ a few thousand rows).
        for tid, kinds in labels.items():
            conn.execute(
                tbl_inventory_t.update()
                .where(tbl_inventory_t.c.table_id == tid)
                .values(
                    subject_kinds=sorted(kinds),
                    subject_link_distance=int(distance.get(tid, 0)),
                )
            )

    # The orchestrator's _run_phase wrapper inserts into run_log; we must
    # not duplicate or the unique constraint on (phase, scope, scope_id)
    # rolls back our entire transaction (including the UPDATE writes).
    max_dist = max(distance.values()) if distance else 0
    result = {
        "tables_tagged": len(labels),
        "roots_seeded": len(roots),
        "max_distance": max_dist,
    }
    log.info("phase_pii_propagation.complete", **result)
    return result
