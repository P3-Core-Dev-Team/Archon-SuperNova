"""Cross-cluster PII leak detector.

Uses HyperMinHash sketches already stored in ``col_inventory.sketch_blob`` to
cheaply identify columns whose value sets overlap with PII direct-identifier
columns from a *different* table.  Containment >= 0.5 is reported as a
potential leak -- a non-PII column carries values that look like PII data.

No new sampling, no extra DuckDB scans.  This is a pure post-processing
step that mines existing fingerprint output.
"""
from __future__ import annotations

import pickle
from typing import Any, Optional

import structlog
from sqlalchemy import select, text

from discovery.config import AppConfig
from discovery.results_db import (
    col_inventory_t,
    pii_findings_t,
    pii_leaks_t,
    run_log_t,
    tbl_inventory_t,
)
from discovery.pii_propagation import DIRECT_IDENTIFIER_TYPES

log = structlog.get_logger(__name__)


def _jaccard(sk1: Any, sk2: Any) -> float:
    """Compute Jaccard from two HyperMinHash / MinHash sketches.

    Both sketches expose ``.jaccard(other)``; if one of them was pickled by
    a different version we fall through to 0.0 instead of crashing.
    """
    try:
        return float(sk1.jaccard(sk2))
    except Exception:  # pragma: no cover -- defensive
        return 0.0


def _containment_from_jaccard(
    j: float, child_distinct: int, parent_distinct: int
) -> float:
    if child_distinct <= 0 or parent_distinct <= 0:
        return 0.0
    union = (child_distinct + parent_distinct) / max(1.0 + j, 1e-9)
    inter = j * union
    return min(1.0, max(0.0, inter / child_distinct))


def run_phase_pii_leak(
    engine,
    config: AppConfig,
    *,
    score_floor: float = 0.85,
    containment_floor: float = 0.5,
) -> dict:
    """Detect PII value-set leaks across cluster boundaries.

    Scoring: for each PII source column (``score >= score_floor`` AND
    pii_type in DIRECT_IDENTIFIER_TYPES) compute containment of every
    OTHER (non-PII) column's sketch into the source.  If containment >=
    ``containment_floor``, persist a leak finding.

    Same-table comparisons are skipped (a table referencing its own PII
    column is expected, not a leak).
    """
    log.info("phase_pii_leak.starting", score_floor=score_floor,
             containment_floor=containment_floor)

    with engine.begin() as conn:
        # Clear prior leaks (idempotent).
        conn.execute(pii_leaks_t.delete())

        # 1. PII source columns (direct identifiers, high score).
        # Deduplicate to unique column_ids: a single column can have multiple
        # pii_findings rows (e.g. EMAIL + PERSON_NAME both in
        # DIRECT_IDENTIFIER_TYPES).  Iterating duplicates produces identical
        # (source_col_id, target_col_id, "value_overlap") pairs which violate
        # the unique constraint on pii_leaks.
        sources_raw = conn.execute(
            select(
                pii_findings_t.c.column_id,
                pii_findings_t.c.pii_type,
            ).where(
                pii_findings_t.c.column_id.is_not(None),
                pii_findings_t.c.score >= score_floor,
                pii_findings_t.c.pii_type.in_(list(DIRECT_IDENTIFIER_TYPES)),
            )
        ).fetchall()
        # Keep only unique column_ids (pii_type is not used in the insert).
        seen_source_ids: set[int] = set()
        sources: list[Any] = []
        for row in sources_raw:
            if int(row[0]) not in seen_source_ids:
                seen_source_ids.add(int(row[0]))
                sources.append(row)

        if not sources:
            log.info("phase_pii_leak.no_sources")
            return {"sources": 0, "leaks": 0}

        # Tagged set: (column_id) of every column that already has a PII
        # finding -- those columns are excluded from the *target* side.
        pii_tagged = {
            row[0] for row in conn.execute(
                select(pii_findings_t.c.column_id).where(
                    pii_findings_t.c.column_id.is_not(None)
                )
            )
        }

        # 2. Load every column with a sketch + its (table_id, distinct_count).
        cols = conn.execute(
            select(
                col_inventory_t.c.column_id,
                col_inventory_t.c.table_id,
                col_inventory_t.c.distinct_count,
                col_inventory_t.c.sketch_blob,
                col_inventory_t.c.type_class,
            ).where(col_inventory_t.c.sketch_blob.is_not(None))
        ).fetchall()

        # Hydrate sketches once.
        cols_meta: dict[int, dict] = {}
        for cid, tid, dc, blob, type_class in cols:
            try:
                sketch = pickle.loads(bytes(blob)) if blob else None
            except Exception:
                sketch = None
            if sketch is None:
                continue
            cols_meta[int(cid)] = {
                "table_id": int(tid),
                "distinct": int(dc) if dc else 0,
                "sketch": sketch,
                "type_class": type_class,
            }

        leaks_inserted = 0
        for source_col_id, source_pii_type in sources:
            src = cols_meta.get(int(source_col_id))
            if src is None or src["distinct"] <= 0:
                continue

            for target_col_id, meta in cols_meta.items():
                if target_col_id == source_col_id:
                    continue
                if target_col_id in pii_tagged:
                    # Both sides PII-tagged -- containment between two PII
                    # columns isn't a "leak"; it's expected schema overlap.
                    continue
                if meta["table_id"] == src["table_id"]:
                    # Same table = same row stream; not a leak.
                    continue
                # Type-class compatibility -- a sketch over INT values vs
                # a sketch over TEXT values has no semantic overlap.
                if meta["type_class"] != src["type_class"]:
                    continue
                if meta["distinct"] <= 0:
                    continue

                j = _jaccard(meta["sketch"], src["sketch"])
                if j <= 0.0:
                    continue
                cont = _containment_from_jaccard(j, meta["distinct"], src["distinct"])
                if cont < containment_floor:
                    continue

                conn.execute(pii_leaks_t.insert().values(
                    source_col_id=source_col_id,
                    target_col_id=target_col_id,
                    containment=float(cont),
                    leak_kind="value_overlap",
                ))
                leaks_inserted += 1

    # Orchestrator's _run_phase wrapper writes to run_log; do not insert here.
    result = {"sources": len(sources), "leaks": leaks_inserted}
    log.info("phase_pii_leak.complete", **result)
    return result
