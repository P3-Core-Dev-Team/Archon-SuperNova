"""
Phase ``cardinality_refine`` — live source-side cardinality probe.

Runs after Phase 5 (validate).  For high-confidence FK relationships,
asks the Java extraction service to return ``COUNT(*)`` and
``COUNT(DISTINCT col)`` directly from the source DB; uses those
numbers to refine the ``cardinality`` field that Phase 5 derived from
DuckDB-on-parquet counts.

Why this matters
----------------
Phase 5's cardinality is computed from the EXTRACTED parquet's
DISTINCT counts.  When extraction was sampled (e.g. TABLESAMPLE 1%
on a 100M-row table), those counts are also sampled — fine for FK
discovery (Jaccard containment is robust to sampling) but not
authoritative for ``ONE_TO_ONE`` vs ``MANY_TO_ONE`` classification.
A live probe gives us the exact totals.

What we can refine
------------------
Limited by what the probe response carries.  The service returns
``total_rows`` and ``distinct_count`` for the CHILD column only —
no orphan count, no parent's distinct values.  So we can only
authoritatively flip relationships where the parquet already proved
``orphans == 0`` (i.e. ``MANY_TO_ONE``): if ``distinct == total_rows``
in the live probe, every child row has a unique value AND every value
maps to a parent → ``ONE_TO_ONE``.  Other buckets (``PARTIAL`` /
``NO_RELATIONSHIP``) are deliberately left alone — flipping them
without orphan evidence would be lossy.

Operator gates
--------------
Default OFF.  Three config knobs:

* ``RelationshipsConfig.cardinality_refine_enabled`` — master switch.
* ``RelationshipsConfig.cardinality_refine_confidence_floor`` — only
  refine relationships at or above this confidence (default 0.85).
* ``RelationshipsConfig.cardinality_refine_batch_size`` — pairs per
  HTTP request (default 50).

Failure mode: if the extraction service is unreachable or returns
404 (older service without ``/probe-cardinality``), the phase logs
``cardinality_refine_skipped`` and exits cleanly — Phase 5's
parquet-derived cardinality stays in place, no rows are touched.

Identifier safety
-----------------
The pipeline sends ``(schema, table, column)`` triples.  Identifier
validation / quoting happens server-side in the extraction service
(``ExtractionService.probeCardinality`` runs them through the strict
``[A-Za-z_][A-Za-z0-9_$]*`` regex before splicing into SQL).  Python
sends names verbatim and trusts the service to reject anything unsafe.
"""

from __future__ import annotations

from typing import Any, Iterable

import structlog

from discovery.fallbacks import chunked

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers (testable in isolation)
# ---------------------------------------------------------------------------


def _eligible_relationships(
    raw_rows: Iterable[dict[str, Any]],
    *,
    confidence_floor: float,
) -> list[dict[str, Any]]:
    """Filter rows to those eligible for live cardinality refinement.

    A relationship is eligible when:
      * confidence is not NULL,
      * confidence >= ``confidence_floor``, AND
      * Phase 5 classified it as ``MANY_TO_ONE``.

    Why MANY_TO_ONE only?  See the module docstring.  In short: the
    probe response gives us child total + child distinct, no orphan
    count.  ``MANY_TO_ONE`` implies orphans were already 0 — so a
    live ``distinct == total`` is sufficient to conclude
    ``ONE_TO_ONE``.  ``PARTIAL`` / ``NO_RELATIONSHIP`` carry orphan
    evidence the probe can't confirm; flipping them would be lossy.
    """
    out: list[dict[str, Any]] = []
    for r in raw_rows:
        c = r.get("confidence")
        if c is None or float(c) < confidence_floor:
            continue
        if r.get("cardinality") != "MANY_TO_ONE":
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Phase entry point
# ---------------------------------------------------------------------------


def run_phase_cardinality_refine(engine: Any, config: Any) -> dict[str, int]:
    """Optional Phase 7 — refine cardinality of high-confidence FKs.

    Returns a dict with counts:
      {probed, refined, skipped_unreachable, skipped_no_change}
    """
    rel_cfg = getattr(config, "relationships", None)
    if not bool(getattr(rel_cfg, "cardinality_refine_enabled", False)):
        log.info("cardinality_refine_disabled")
        return {"probed": 0, "refined": 0, "skipped_unreachable": 0, "skipped_no_change": 0}

    confidence_floor = float(
        getattr(rel_cfg, "cardinality_refine_confidence_floor", 0.85)
    )
    batch_size = int(getattr(rel_cfg, "cardinality_refine_batch_size", 50))

    # Lazy imports — keep pipeline startup cheap when this phase is off.
    from sqlalchemy import select, update  # noqa: PLC0415

    from discovery.extraction_client import ExtractionClient  # noqa: PLC0415
    from discovery.results_db import (  # noqa: PLC0415
        col_inventory_t,
        relationships_t,
        tbl_inventory_t,
        txn,
    )

    # Pull every relationship + child / parent table & column metadata in
    # one pass so we can build the (schema, table, column) probe pairs.
    with engine.connect() as conn:
        stmt = (
            select(
                relationships_t.c.rel_id,
                relationships_t.c.confidence,
                relationships_t.c.cardinality,
                col_inventory_t.c.column_id.label("child_col_id"),
                tbl_inventory_t.c.schema_name.label("child_schema"),
                tbl_inventory_t.c.table_name.label("child_table"),
                col_inventory_t.c.column_name.label("child_column"),
            )
            .select_from(
                relationships_t.join(
                    col_inventory_t,
                    col_inventory_t.c.column_id == relationships_t.c.child_col_id,
                ).join(
                    tbl_inventory_t,
                    tbl_inventory_t.c.table_id == col_inventory_t.c.table_id,
                )
            )
        )
        raw_rows = [dict(r) for r in conn.execute(stmt).mappings().all()]

    eligible = _eligible_relationships(
        raw_rows, confidence_floor=confidence_floor,
    )
    if not eligible:
        log.info("cardinality_refine_no_eligible_rows", total=len(raw_rows))
        return {"probed": 0, "refined": 0, "skipped_unreachable": 0, "skipped_no_change": 0}

    log.info(
        "cardinality_refine_start",
        eligible=len(eligible),
        confidence_floor=confidence_floor,
        batch_size=batch_size,
    )

    # Build the probe-pair list.  Keys are stringified so the JSON
    # payload is stable across (rel_id, schema, table, column) tuples
    # and we can match service responses back to rows by name.
    pairs = [
        {
            "schema": str(r["child_schema"]),
            "table":  str(r["child_table"]),
            "column": str(r["child_column"]),
        }
        for r in eligible
    ]
    # De-duplicate — many relationships can share the same child column
    # (composite parent variants etc.); one probe per column saves
    # round-trips to the source DB.
    seen: set[tuple[str, str, str]] = set()
    unique_pairs: list[dict[str, str]] = []
    for p in pairs:
        key = (p["schema"], p["table"], p["column"])
        if key in seen:
            continue
        seen.add(key)
        unique_pairs.append(p)

    # Open the extraction client.  If construction fails (config
    # missing, etc.) treat it as an unreachable service and exit
    # gracefully — the pipeline doesn't depend on this phase.
    try:
        ext_cfg = config.extraction_service
        client = ExtractionClient(
            base_url=ext_cfg.base_url,
            auth_token=ext_cfg.auth_token,
            request_timeout_seconds=int(getattr(ext_cfg, "request_timeout_seconds", 7200)),
        )
        source_conn = config.source_db.to_connection_config()
    except Exception as exc:
        log.warning(
            "cardinality_refine_client_unavailable",
            error=str(exc),
        )
        return {"probed": 0, "refined": 0, "skipped_unreachable": len(eligible), "skipped_no_change": 0}

    probed = 0
    skipped_unreachable = 0
    results_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    try:
        for batch in chunked(unique_pairs, batch_size):
            try:
                resp = client.probe_cardinality(source_conn, batch)
            except Exception as exc:
                log.warning(
                    "cardinality_refine_batch_failed",
                    error=str(exc),
                    batch_size=len(batch),
                )
                skipped_unreachable += len(batch)
                continue
            probed += len(resp)
            for r in resp:
                key = (str(r.get("schema")), str(r.get("table")), str(r.get("column")))
                results_by_key[key] = r
    finally:
        client.close()

    if not results_by_key:
        log.warning("cardinality_refine_no_results")
        return {
            "probed": probed,
            "refined": 0,
            "skipped_unreachable": skipped_unreachable,
            "skipped_no_change": 0,
        }

    # Apply refinements — flip MANY_TO_ONE → ONE_TO_ONE only when the
    # live probe proves the child column is unique
    # (``distinct == total_rows``).  Phase 5 already proved orphans=0
    # for MANY_TO_ONE, so unique child values + zero orphans → 1:1.
    #
    # Transaction grain: commit every ``COMMIT_CHUNK`` rows so a
    # service hiccup mid-phase doesn't roll back hundreds of valid
    # refinements.  Per-row commit would be safer but ~50× slower on
    # large schemas; 50 strikes the balance.
    COMMIT_CHUNK = 50
    refined = 0
    skipped_no_change = 0
    pending: list[int] = []  # rel_ids queued for the next flush

    def _flush(rel_ids: list[int]) -> None:
        if not rel_ids:
            return
        with txn(engine) as conn:
            conn.execute(
                update(relationships_t)
                .where(relationships_t.c.rel_id.in_(rel_ids))
                .values(cardinality="ONE_TO_ONE")
            )

    for r in eligible:
        key = (
            str(r["child_schema"]),
            str(r["child_table"]),
            str(r["child_column"]),
        )
        payload = results_by_key.get(key)
        if not payload:
            continue
        total_rows = int(payload.get("total_rows") or 0)
        distinct = int(payload.get("distinct_count") or 0)
        if total_rows == 0 or distinct == 0:
            skipped_no_change += 1
            continue
        if distinct != total_rows:
            # MANY_TO_ONE confirmed (duplicates in child column) — the
            # parquet bucket is already correct; nothing to flip.
            skipped_no_change += 1
            continue
        # Eligible filter guarantees current cardinality is MANY_TO_ONE,
        # so this is genuinely a flip.
        pending.append(int(r["rel_id"]))
        refined += 1
        if len(pending) >= COMMIT_CHUNK:
            _flush(pending)
            pending.clear()
    _flush(pending)

    log.info(
        "cardinality_refine_done",
        probed=probed,
        refined=refined,
        skipped_unreachable=skipped_unreachable,
        skipped_no_change=skipped_no_change,
    )
    return {
        "probed": probed,
        "refined": refined,
        "skipped_unreachable": skipped_unreachable,
        "skipped_no_change": skipped_no_change,
    }
