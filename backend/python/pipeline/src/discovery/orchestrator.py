"""
orchestrator.py — run_all coordinator for the Discovery pipeline.

``run_all`` drives Phases 1 → 7 sequentially, using ``run_log`` to skip
phases that have already completed (full resumability).

Two-pass entry point
--------------------
``run_all_two_pass`` implements C2 from the minimal-scan plan:

1. Phase 1 inventory
2. Phase 2a SAMPLE extract (TABLESAMPLE BERNOULLI(p))
3. Phase 3a fingerprint on sampled parquet
4. Phase 3b PII scan on sampled parquet (CAVEAT: regex/validator results may
   be noisier on a 1% sample — long-tail PII can be missed; document this in
   release notes; consider a post-hoc full scan for STRING_LONG columns)
5. Phase 4 candidate generation on sampled fingerprints
6. Determine which tables (child + parent) appear in surviving fk_candidates
7. Phase 2b FULL extract for those tables only (mode='full_subset',
   ``table_ids=...``); overwrites the same Parquet path used in Phase 2a
8. Re-fingerprint just the affected columns (clear ``fingerprinted_at``)
9. Phase 5 validate (full data for survivors, sampled for non-survivors;
   non-survivors don't matter — Phase 5 only validates pairs in fk_candidates)
10. Phase 7 report

Coordination notes
------------------
* Phase 2 (extraction) is owned by C3 — this module CALLS its yet-to-land
  ``mode='sample' | 'full_subset'`` and ``table_ids=...`` parameters.  When
  C3 lands, those kwargs flow through; until then the call signature surfaces
  the deferred dependency clearly.
* Phase 2a and Phase 2b WRITE TO THE SAME PARQUET PATH (no .sample.parquet
  suffix).  The full extract overwrites.  C3 must produce same-path output
  for both modes.
* ``_reset_fingerprint_state`` NULLs ``col_inventory.fingerprinted_at`` for
  the affected tables; Phase 3a's resume guard then re-processes them.

Design notes
------------
* Each phase is wrapped with run_log start/succeed/fail semantics.
* If ``run_log.is_complete`` returns True for a phase, it is logged and
  skipped — no re-work.
* Exceptions propagate after being logged; the caller (cli.py) is responsible
  for catching them and exiting with a non-zero code.
* Retries are NOT applied at phase level here.  Individual operations inside
  the phase modules carry their own tenacity retry decorators.
* ``skip_phases`` accepts phase names matching the run_log ``phase`` column
  values: "inventory", "extract", "fingerprint", "pii_scan", "candidate_gen",
  "validate", "report".
* ``limit`` is forwarded to Phase 2 (extract) and Phase 5 (validate) only.
"""
from __future__ import annotations

from typing import Any

import structlog

from discovery import metrics

log = structlog.get_logger(__name__)

# Phase name constants — must match the run_log.phase column values.
PHASE_INVENTORY = "inventory"
PHASE_EXTRACT = "extract"
PHASE_FINGERPRINT = "fingerprint"
PHASE_PII_SCAN = "pii_scan"
PHASE_CANDIDATE_GEN = "candidate_gen"
PHASE_VALIDATE = "validate"
# New advanced FK detector phases (run after validate, before report).
PHASE_COMPOSITE_FK = "composite_fk"
PHASE_POLYMORPHIC_FK = "polymorphic_fk"
PHASE_JSONB_FK = "jsonb_fk"
PHASE_INHERITANCE = "inheritance"
PHASE_PII_PROPAGATION = "pii_propagation"
PHASE_PII_LEAK = "pii_leak"
PHASE_CLUSTERING = "clustering"
PHASE_REPORT = "report"

ALL_PHASES = [
    PHASE_INVENTORY,
    PHASE_EXTRACT,
    PHASE_FINGERPRINT,
    PHASE_PII_SCAN,
    PHASE_CANDIDATE_GEN,
    PHASE_VALIDATE,
    PHASE_COMPOSITE_FK,
    PHASE_POLYMORPHIC_FK,
    PHASE_JSONB_FK,
    PHASE_INHERITANCE,
    PHASE_PII_PROPAGATION,
    PHASE_PII_LEAK,
    PHASE_CLUSTERING,
    PHASE_REPORT,
]

# Backwards-compatibility alias (older callers used the underscored name).
_ALL_PHASES = ALL_PHASES


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _run_phase(
    phase: str,
    run_log: Any,
    skip_phases: list[str],
    fn: Any,
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Execute a single pipeline phase with run_log tracking.

    * If the phase name is in *skip_phases*, skip it entirely.
    * If run_log reports the phase already complete, log and skip.
    * Otherwise: start → call fn → succeed on return / fail on exception.
    """
    if phase in skip_phases:
        log.info("phase_skipped_by_flag", phase=phase)
        return

    if run_log.is_complete(phase, "global", None):
        log.info("phase_already_complete_skipping", phase=phase)
        return

    log.info("phase_starting", phase=phase)
    run_log.start(phase, "global", None)
    try:
        with metrics.TASK_DURATION.labels(phase=phase).time():
            fn(*args, **kwargs)
        run_log.succeed(phase, "global", None)
        metrics.TASKS_TOTAL.labels(phase=phase, status="success").inc()
        log.info("phase_complete", phase=phase)
    except Exception as exc:
        log.error("phase_failed", phase=phase, error=str(exc), exc_info=True)
        run_log.fail(phase, "global", None, str(exc))
        metrics.TASKS_TOTAL.labels(phase=phase, status="failure").inc()
        raise


def _enforce_disk_cap_safely(config: Any, engine: Any) -> None:
    """
    Best-effort post-Phase-2 disk-cap check.

    Never raises — disk-cap enforcement is advisory.  Failures are logged
    and the pipeline continues.
    """
    try:
        from discovery.cleanup import enforce_disk_cap  # noqa: PLC0415

        enforce_disk_cap(config, engine=engine)
    except Exception as exc:  # pragma: no cover - defence-in-depth
        log.warning("disk_cap_check_failed", error=str(exc))


def _reset_fingerprint_state(engine: Any, table_ids: list[int]) -> int:
    """
    Clear ``col_inventory.fingerprinted_at`` for every column belonging to one
    of *table_ids*.  Phase 3a's resume logic then re-processes them.

    Returns the number of column rows reset.
    """
    if not table_ids:
        return 0

    from sqlalchemy import update  # noqa: PLC0415

    from discovery.results_db import col_inventory_t, txn  # noqa: PLC0415

    stmt = (
        update(col_inventory_t)
        .where(col_inventory_t.c.table_id.in_(list(table_ids)))
        .values(fingerprinted_at=None)
    )
    with txn(engine) as conn:
        result = conn.execute(stmt)
        rowcount = result.rowcount or 0

    log.info(
        "fingerprint_state_reset",
        table_count=len(table_ids),
        column_count=rowcount,
    )
    return rowcount


def _surviving_candidate_table_ids(engine: Any) -> list[int]:
    """
    Return the union of child + parent table_ids referenced by surviving
    fk_candidates.  These are the tables Phase 2b must re-extract at full
    resolution.
    """
    from sqlalchemy import select, union  # noqa: PLC0415

    from discovery.results_db import (  # noqa: PLC0415
        col_inventory_t,
        fk_candidates_t,
    )

    child_q = (
        select(col_inventory_t.c.table_id)
        .select_from(
            fk_candidates_t.join(
                col_inventory_t,
                col_inventory_t.c.column_id == fk_candidates_t.c.child_col_id,
            )
        )
    )
    parent_q = (
        select(col_inventory_t.c.table_id)
        .select_from(
            fk_candidates_t.join(
                col_inventory_t,
                col_inventory_t.c.column_id == fk_candidates_t.c.parent_col_id,
            )
        )
    )
    stmt = union(child_q, parent_q)

    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    ids = sorted({int(r[0]) for r in rows})
    log.info("surviving_candidate_tables", count=len(ids))
    return ids


# ---------------------------------------------------------------------------
# Clustering phase runner
# ---------------------------------------------------------------------------


def run_phase_clustering(engine: Any, config: Any) -> dict:
    """Cluster tables within each schema and persist results.

    For each distinct schema_name in tbl_inventory:
      1. Load tables, columns, edges, pii_findings from the results DB.
      2. Call ``discovery.clustering.cluster_schema`` (CL-1's API).
      3. Persist via the Cluster DAO (clear-then-insert for idempotency).

    Returns
    -------
    dict with keys:
        schemas_processed: int
        clusters_total: int
        junctions_collapsed: int
    """
    from sqlalchemy import select, text as _text  # noqa: PLC0415

    from discovery import clustering as _clustering  # noqa: PLC0415
    from discovery.results_db import (  # noqa: PLC0415
        Cluster as ClusterDAO,
        col_inventory_t,
        pii_findings_t,
        relationships_t,
        tbl_inventory_t,
        txn,
    )

    schemas_processed = 0
    clusters_total = 0
    junctions_collapsed = 0

    rel_cfg = getattr(config, "relationships", None)
    # Use cluster-specific confidence floor if set; otherwise default to 0.7
    # (CL-1's algorithm default).  Do NOT use containment_threshold (0.95) — that
    # is the FK-validation gate and would discard most edges before Louvain runs.
    confidence_floor = float(getattr(rel_cfg, "clustering_confidence_floor", 0.7))
    # Hybrid clustering knobs (Sprint 3) — feed the semantic-merge +
    # zero-shot-label config through to ``cluster_schema``.  Defaults
    # match RelationshipsConfig so a missing rel_cfg still works.
    semantic_merge_enabled = bool(getattr(rel_cfg, "semantic_merge_enabled", True))
    semantic_merge_threshold = float(getattr(rel_cfg, "semantic_merge_threshold", 0.65))
    semantic_merge_modularity_floor = float(
        getattr(rel_cfg, "semantic_merge_modularity_floor", 0.95)
    )
    semantic_label_enabled = bool(getattr(rel_cfg, "semantic_label_enabled", True))
    semantic_label_threshold = float(getattr(rel_cfg, "semantic_label_threshold", 0.55))

    # Discover distinct schema names.
    with engine.connect() as conn:
        schema_rows = conn.execute(
            select(tbl_inventory_t.c.schema_name).distinct()
        ).all()
    schema_names = [r[0] for r in schema_rows]

    for schema_name in schema_names:
        log.info("clustering_schema_starting", schema_name=schema_name)

        with engine.connect() as conn:
            tables = [
                dict(r)
                for r in conn.execute(
                    select(tbl_inventory_t).where(
                        tbl_inventory_t.c.schema_name == schema_name
                    )
                ).mappings().all()
            ]
            if not tables:
                continue

            table_ids = [t["table_id"] for t in tables]

            columns = [
                dict(r)
                for r in conn.execute(
                    select(col_inventory_t).where(
                        col_inventory_t.c.table_id.in_(table_ids)
                    )
                ).mappings().all()
            ]

            # Load single-column edges; CL-1 accepts dicts.
            edges = [
                dict(r)
                for r in conn.execute(
                    select(relationships_t).where(
                        relationships_t.c.child_col_id.in_(
                            select(col_inventory_t.c.column_id).where(
                                col_inventory_t.c.table_id.in_(table_ids)
                            )
                        )
                    )
                ).mappings().all()
            ]

            pii_findings = [
                dict(r)
                for r in conn.execute(
                    select(pii_findings_t).where(
                        pii_findings_t.c.table_id.in_(table_ids)
                    )
                ).mappings().all()
            ]

        # Call CL-1's algorithm.
        result = _clustering.cluster_schema(
            schema_name=schema_name,
            tables=tables,
            columns=columns,
            edges=edges,
            pii_findings=pii_findings,
            confidence_floor=confidence_floor,
            seed=42,
            semantic_merge_enabled=semantic_merge_enabled,
            semantic_merge_threshold=semantic_merge_threshold,
            semantic_merge_modularity_floor=semantic_merge_modularity_floor,
            semantic_label_enabled=semantic_label_enabled,
            semantic_label_threshold=semantic_label_threshold,
        )

        if not result.clusters:
            log.info("clustering_schema_no_clusters", schema_name=schema_name)
            schemas_processed += 1
            continue

        # Build cluster dicts for the DAO.
        # junction_collapsed is a tuple of table_ids on ClusteringResult (not per-table).
        junction_collapsed_set: set[int] = set(
            int(j) for j in (result.junction_collapsed or ())
        )
        schema_junctions = len(junction_collapsed_set)

        cluster_dicts = [
            {
                "cluster_local_id": c.cluster_id,  # CL-1's 0-indexed id
                "name": c.name,
                "table_count": len(c.table_ids),
                "intra_edge_count": int(getattr(c, "intra_edge_count", 0)),
                "inter_edge_count": int(getattr(c, "inter_edge_count", 0)),
                # CL-1 uses modularity_contribution per Cluster; fall back to
                # the overall result.modularity_score if missing.
                "modularity_score": (
                    getattr(c, "modularity_contribution", None)
                    or getattr(c, "modularity_score", None)
                ),
                "archetype_distribution": (
                    c.archetype_distribution
                    if isinstance(c.archetype_distribution, dict)
                    else dict(c.archetype_distribution)
                ),
                "member_table_ids": list(c.table_ids),
                # Sprint 3: optional zero-shot domain label.  None
                # propagates as NULL — the UI falls back to ``name``.
                "semantic_label": getattr(c, "semantic_label", None),
            }
            for c in result.clusters
        ]

        assignment_dicts = [
            {
                "table_id": a.table_id,
                "cluster_id": a.cluster_id,  # CL-1's local index
                "archetype": a.archetype,
                # ClusteredTable doesn't carry junction_collapsed; use the set.
                "junction_collapsed": a.table_id in junction_collapsed_set,
            }
            for a in result.table_assignments
        ]

        with txn(engine) as conn:
            dao = ClusterDAO(conn)
            dao.clear_clusters(schema_name)
            local_to_pk = dao.insert_clusters(schema_name, cluster_dicts)
            dao.update_table_assignments(assignment_dicts, local_to_pk=local_to_pk)

        clusters_total += len(result.clusters)
        junctions_collapsed += schema_junctions
        schemas_processed += 1

        log.info(
            "clustering_schema_complete",
            schema_name=schema_name,
            clusters=len(result.clusters),
            junctions=schema_junctions,
        )

    return {
        "schemas_processed": schemas_processed,
        "clusters_total": clusters_total,
        "junctions_collapsed": junctions_collapsed,
    }


# ---------------------------------------------------------------------------
# Advanced FK detector phases (composite, polymorphic, jsonb, inheritance)
# ---------------------------------------------------------------------------


def _run_advanced_fk_phases(
    engine: Any,
    config: Any,
    run_log: Any,
    skip_phases: list[str],
) -> None:
    """Run the four post-Phase-5 detectors with config gating + run_log.

    Each of composite / polymorphic / jsonb / inheritance is wrapped with
    ``_run_phase`` so resumption + run_log are consistent with the rest of
    the pipeline.  Modules are imported at function scope to keep startup
    cheap and to avoid a hard dep when only some agents have landed code.
    """
    rel_cfg = getattr(config, "relationships", None)
    pii_cfg = getattr(config, "pii", None)

    # Stage-level fallback policy — when False, an exception in one of
    # these optional phases re-raises and aborts the pipeline (fail-fast
    # mode used for CI / debugging).  Default True keeps the pre-existing
    # "log + continue" behaviour.
    orch_cfg = getattr(config, "orchestration", None)
    enable_fallbacks = bool(getattr(orch_cfg, "enable_phase_fallbacks", True))

    def _run_optional(
        gate_attr: str,
        gate_owner: Any,
        gate_default: bool,
        phase_label: str,
        phase_const: str,
        module_name: str,
        fn_attr: str,
    ) -> None:
        """Wrap one optional phase: gate -> import -> _run_phase, with
        the canonical "phase_skipped" log on exception.  Honours
        ``enable_phase_fallbacks`` — re-raises on False so callers fail
        fast instead of silently degrading."""
        if not bool(getattr(gate_owner, gate_attr, gate_default)):
            return
        try:
            mod = __import__(f"discovery.{module_name}", fromlist=[fn_attr])
            _run_phase(
                phase_const,
                run_log,
                skip_phases,
                getattr(mod, fn_attr),
                engine,
                config,
            )
        except Exception as exc:
            log.warning(f"{phase_label}_phase_skipped", error=str(exc))
            if not enable_fallbacks:
                raise

    # Composite (Phase 4b).  ``composite_fk_enabled`` defaults True now
    # that composite_fk is folded into run-all.
    _run_optional(
        "composite_fk_enabled", rel_cfg, True,
        "composite_fk", PHASE_COMPOSITE_FK,
        "composite_fk", "run_phase_4b_composite",
    )
    # Polymorphic (Phase 4c).
    _run_optional(
        "polymorphic_fk_enabled", rel_cfg, True,
        "polymorphic_fk", PHASE_POLYMORPHIC_FK,
        "polymorphic_fk", "run_phase_polymorphic_fk",
    )
    # JSONB (Phase 4d).
    _run_optional(
        "jsonb_fk_enabled", rel_cfg, True,
        "jsonb_fk", PHASE_JSONB_FK,
        "jsonb_fk", "run_phase_jsonb_fk",
    )
    # Inheritance annotator (post-Phase-5 evidence merge).
    _run_optional(
        "inheritance_annotator_enabled", rel_cfg, True,
        "inheritance", PHASE_INHERITANCE,
        "inheritance", "run_phase_inheritance",
    )
    # Subject-rooted PII propagation (reverse-BFS over relationships).
    _run_optional(
        "propagation_enabled", pii_cfg, True,
        "pii_propagation", PHASE_PII_PROPAGATION,
        "pii_propagation", "run_phase_pii_propagation",
    )
    # Cross-cluster PII leak detector (sketch-based containment).
    _run_optional(
        "leak_scan_enabled", pii_cfg, True,
        "pii_leak", PHASE_PII_LEAK,
        "pii_leak", "run_phase_pii_leak",
    )
    # Schema clustering (groups tables into cohesive clusters per schema).
    # Special-case: clustering's runner is a local function in this
    # module (run_phase_clustering), not an attribute of an external
    # module — handle inline rather than via _run_optional.
    if bool(getattr(rel_cfg, "clustering_enabled", True)):
        try:
            from discovery import clustering, results_db  # noqa: PLC0415, F401

            _run_phase(
                PHASE_CLUSTERING,
                run_log,
                skip_phases,
                run_phase_clustering,
                engine,
                config,
            )
        except Exception as exc:
            log.warning("clustering_phase_skipped", error=str(exc))
            if not enable_fallbacks:
                raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_all(
    config: Any,
    limit: int | None = None,
    skip_phases: list[str] | None = None,
) -> None:
    """
    Execute all pipeline phases in order, skipping completed ones.

    Parameters
    ----------
    config:
        Loaded pipeline configuration object (from ``config.load_config``).
    limit:
        Optional row/table limit forwarded to Phase 2 and Phase 5.
    skip_phases:
        List of phase names to skip unconditionally.
    """
    if skip_phases is None:
        skip_phases = []

    # Deferred imports — keeps module import cheap and avoids circular paths.
    from discovery import (  # noqa: PLC0415
        candidates,
        extraction,
        fingerprint,
        inventory,
        pii_scan,
        report,
        validate,
    )
    from discovery.extraction_client import ExtractionClient  # noqa: PLC0415
    from discovery.results_db import get_engine  # noqa: PLC0415
    from discovery.run_log import RunLog  # noqa: PLC0415

    engine = get_engine(config.results_db)
    run_log = RunLog(engine)

    # Start the Prometheus metrics endpoint once per process.  The helper
    # already swallows OSError on rebind, but defensively guard the call so
    # an unexpected failure here never aborts the pipeline.
    metrics_port = getattr(
        getattr(config, "observability", None), "metrics_port", 9009
    )
    try:
        metrics.start_metrics_server(port=metrics_port)
    except Exception as exc:  # pragma: no cover - defence-in-depth
        log.warning("metrics_server_start_failed", port=metrics_port, error=str(exc))

    svc_cfg = config.extraction_service
    client = ExtractionClient(
        base_url=svc_cfg.base_url,
        auth_token=svc_cfg.auth_token,
        request_timeout_seconds=svc_cfg.request_timeout_seconds,
    )

    log.info(
        "run_all_starting",
        phases=ALL_PHASES,
        skip_phases=skip_phases,
        limit=limit,
    )

    # Phase 1 — Inventory
    _run_phase(
        PHASE_INVENTORY,
        run_log,
        skip_phases,
        inventory.run_phase_1,
        engine,
        client,
        config,
    )

    # Phase 2 — Full Extraction
    _run_phase(
        PHASE_EXTRACT,
        run_log,
        skip_phases,
        extraction.run_phase_2,
        engine,
        client,
        config,
        limit=limit,
    )

    # Disk-cap advisory after extraction (E5).  Non-blocking — warns only.
    _enforce_disk_cap_safely(config, engine)

    # Phase 3a — Fingerprint
    _run_phase(
        PHASE_FINGERPRINT,
        run_log,
        skip_phases,
        fingerprint.run_phase_3a,
        engine,
        config,
    )

    # Phase 3b — PII Scan
    _run_phase(
        PHASE_PII_SCAN,
        run_log,
        skip_phases,
        pii_scan.run_phase_3b,
        engine,
        config,
    )

    # Phase 4 — Generate Candidates
    _run_phase(
        PHASE_CANDIDATE_GEN,
        run_log,
        skip_phases,
        candidates.run_phase_4,
        engine,
        config,
    )

    # Phase 5 — Validate
    _run_phase(
        PHASE_VALIDATE,
        run_log,
        skip_phases,
        validate.run_phase_5,
        engine,
        config,
        limit=limit,
    )

    # ------------------------------------------------------------------
    # Phase 4b/c/d + inheritance — advanced FK detectors.  Each is
    # individually gated by ``config.relationships.<flag>_enabled``.
    # ``_run_advanced_fk_phases`` handles the gating + run_log wrapping
    # for all four; falling back to a no-op when the module isn't loaded
    # (e.g. partial install) so the rest of run-all keeps working.
    # ------------------------------------------------------------------
    _run_advanced_fk_phases(engine, config, run_log, skip_phases)

    # Phase 7 — Reporting
    _run_phase(
        PHASE_REPORT,
        run_log,
        skip_phases,
        report.generate_all,
        engine,
        config,
    )

    log.info("run_all_complete")


# ---------------------------------------------------------------------------
# Two-pass orchestration (C2: minimal-scan)
# ---------------------------------------------------------------------------


def run_all_two_pass(
    config: Any,
    sample_pct: float = 1.0,
    skip_phases: list[str] | None = None,
) -> None:
    """
    Two-pass orchestration: triage on a small sample, then full-extract only
    the tables touched by surviving candidates.

    Parameters
    ----------
    config:
        Loaded pipeline configuration object.
    sample_pct:
        Per-table TABLESAMPLE BERNOULLI percentage for Phase 2a in the
        half-open range (0, 100] (e.g. ``1.0`` = 1%, ``5.0`` = 5%).  Default
        ``1.0`` (1% — the recommended starting point).  Small tables
        (reltuples <= 100k) are full-extracted by extraction.py regardless.
    skip_phases:
        Optional phase-name list to skip.  Same semantics as ``run_all``.

    Coordination
    ------------
    * Phase 2 is invoked twice — once with ``mode='sample'``, once with
      ``mode='full_subset'``.  Those parameters are added by C3 in
      extraction.py; this module is the consumer.
    * Phase 2 writes to the SAME path on both passes.  Phase 2b overwrites
      Phase 2a's output for the surviving subset.
    * Both calls share a single global-scope run_log entry under
      ``phase='extract'``; the run_log lifecycle is managed by the wrapper
      around the FIRST call, with the second call running outside the
      ``_run_phase`` wrapper so the orchestrator can drive it explicitly.
      Per-table run_log entries inside extraction.py guarantee idempotency.

    PII caveat
    ----------
    Phase 3b regex/validator results on a 1% sample under-detect rare-token
    PII (a single email in 100M ``notes`` rows is missed at p=1%).  The
    minimal-scan doc recommends a follow-up full PII pass over STRING_LONG
    columns; that is NOT implemented here — surface the limitation in the
    operator-facing docs.
    """
    if skip_phases is None:
        skip_phases = []

    # Deferred imports
    from discovery import (  # noqa: PLC0415
        candidates,
        extraction,
        fingerprint,
        inventory,
        pii_scan,
        report,
        validate,
    )
    from discovery.extraction_client import ExtractionClient  # noqa: PLC0415
    from discovery.results_db import get_engine  # noqa: PLC0415
    from discovery.run_log import RunLog  # noqa: PLC0415

    engine = get_engine(config.results_db)
    run_log = RunLog(engine)

    metrics_port = getattr(
        getattr(config, "observability", None), "metrics_port", 9009
    )
    try:
        metrics.start_metrics_server(port=metrics_port)
    except Exception as exc:  # pragma: no cover
        log.warning("metrics_server_start_failed", port=metrics_port, error=str(exc))

    svc_cfg = config.extraction_service
    client = ExtractionClient(
        base_url=svc_cfg.base_url,
        auth_token=svc_cfg.auth_token,
        request_timeout_seconds=svc_cfg.request_timeout_seconds,
    )

    log.info(
        "run_all_two_pass_starting",
        sample_pct=sample_pct,
        skip_phases=skip_phases,
    )

    # ---- 1. Phase 1 — Inventory (full)
    _run_phase(
        PHASE_INVENTORY,
        run_log,
        skip_phases,
        inventory.run_phase_1,
        engine,
        client,
        config,
    )

    # ---- 2. Phase 2a — Sample extract
    # CALLS C3-owned ``mode='sample'`` parameter on extraction.run_phase_2.
    _run_phase(
        PHASE_EXTRACT,
        run_log,
        skip_phases,
        extraction.run_phase_2,
        engine,
        client,
        config,
        mode="sample",
        sample_pct=sample_pct,
    )

    _enforce_disk_cap_safely(config, engine)

    # ---- 3. Phase 3a — Fingerprint on sampled parquet
    _run_phase(
        PHASE_FINGERPRINT,
        run_log,
        skip_phases,
        fingerprint.run_phase_3a,
        engine,
        config,
    )

    # ---- 4. Phase 3b — PII scan on sampled parquet (caveat: noisier results)
    _run_phase(
        PHASE_PII_SCAN,
        run_log,
        skip_phases,
        pii_scan.run_phase_3b,
        engine,
        config,
    )

    # ---- 5. Phase 4 — Candidate generation on sampled fingerprints
    _run_phase(
        PHASE_CANDIDATE_GEN,
        run_log,
        skip_phases,
        candidates.run_phase_4,
        engine,
        config,
    )

    # ---- 6. Determine touched tables (union of child + parent table_ids)
    # Skip the entire 2b → refingerprint block if Phase 5 has already
    # succeeded — otherwise a clean resume re-extracts and re-fingerprints
    # for nothing (Phase 5 itself is gated and won't re-validate).
    if run_log.is_complete(PHASE_VALIDATE, "global", None):
        log.info(
            "two_pass_phase5_already_complete_skipping_2b",
            note="Phase 5 succeeded previously — skipping Phase 2b + re-fingerprint",
        )
        touched_table_ids = []
    else:
        touched_table_ids = _surviving_candidate_table_ids(engine)

    if not touched_table_ids:
        log.info(
            "two_pass_no_survivors",
            note="no fk_candidates survived Phase 4 — skipping Phase 2b/refingerprint/Phase 5",
        )
    else:
        log.info(
            "two_pass_phase2b_starting",
            touched_table_count=len(touched_table_ids),
        )

        # ---- 7. Phase 2b — Re-extract surviving tables in full
        # CALLS C3-owned ``mode='full_subset'`` and ``table_ids=...`` parameters.
        # Runs OUTSIDE the _run_phase wrapper because Phase 2 is already
        # marked succeeded by the Phase 2a call above.  Per-table run_log
        # entries owned inside extraction.py keep idempotency on the
        # subset path.
        log.info("phase_2b_calling", mode="full_subset")
        with metrics.TASK_DURATION.labels(phase=PHASE_EXTRACT).time():
            extraction.run_phase_2(
                engine,
                client,
                config,
                mode="full_subset",
                table_ids=touched_table_ids,
            )

        _enforce_disk_cap_safely(config, engine)

        # ---- 8. Re-fingerprint affected columns
        reset_count = _reset_fingerprint_state(engine, touched_table_ids)
        log.info(
            "phase_3a_re_run_starting",
            tables=len(touched_table_ids),
            columns_reset=reset_count,
        )

        # NOTE: the fingerprint phase has already been marked succeeded by the
        # earlier ``_run_phase`` call.  We invoke it directly so the resume
        # logic (fingerprinted_at IS NULL) re-processes the freshly cleared
        # subset; otherwise ``is_complete('fingerprint','global',None)`` would
        # short-circuit the re-run.
        with metrics.TASK_DURATION.labels(phase=PHASE_FINGERPRINT).time():
            fingerprint.run_phase_3a(engine, config)

    # ---- 9. Phase 5 — Validate (operates on the full parquet for survivors)
    _run_phase(
        PHASE_VALIDATE,
        run_log,
        skip_phases,
        validate.run_phase_5,
        engine,
        config,
    )

    # ---- 9b. Advanced FK detectors (composite / polymorphic / jsonb /
    # inheritance) -- mirror the run_all path so two-pass picks them up too.
    _run_advanced_fk_phases(engine, config, run_log, skip_phases)

    # ---- 10. Phase 7 — Reporting
    _run_phase(
        PHASE_REPORT,
        run_log,
        skip_phases,
        report.generate_all,
        engine,
        config,
    )

    log.info("run_all_two_pass_complete")
