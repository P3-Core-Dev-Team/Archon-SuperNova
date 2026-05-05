"""
cleanup.py — Parquet garbage collection + disk-cap enforcement.

This module implements E5 from the codeflow audit: ``parquet_cap_bytes`` is
declared in ``StorageConfig`` but no code path enforces it.  We add three
helpers:

* :func:`current_parquet_bytes` — sum the on-disk size of all files under
  ``config.storage.base_path``.  Used both by ``enforce_disk_cap`` and the
  CLI ``cleanup`` subcommand for reporting.

* :func:`gc_orphaned_parquet` — delete Parquet files for tables whose
  presence is no longer needed by downstream phases.  The conservative
  rule (per the task spec) is:

      ``status == 'extracted'`` AND the table appears in NEITHER the child
      nor the parent side of any surviving fk_candidate.

  ``status == 'excluded'`` tables are also a no-op target, but Phase 2
  never extracts them so their Parquet files don't exist in practice;
  we still scan and remove any stale orphans found with that status.

* :func:`enforce_disk_cap` — non-blocking advisory that runs orphan GC
  when disk usage exceeds ``parquet_cap_bytes`` and warns on residual
  pressure.  Never raises; always returns a bool.

All public functions emit structlog kwargs only — no printf-style strings.

The CLI subcommand ``discovery cleanup [--keep-results] [--dry-run]`` is
defined in ``cli.py`` and delegates here.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Disk usage helpers
# ---------------------------------------------------------------------------


def current_parquet_bytes(config: Any) -> int:
    """
    Return the sum of file sizes (bytes) under ``config.storage.base_path``.

    Walks the directory recursively.  Symlinks are followed via stat() to
    match the size that actually consumes the underlying filesystem.

    Returns 0 if the path doesn't exist (initial run, or after rmtree).
    """
    base = Path(config.storage.base_path)
    if not base.exists():
        log.debug("parquet_dir_missing", base_path=str(base))
        return 0

    total = 0
    for path in base.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError as exc:  # pragma: no cover - defence-in-depth
            log.warning(
                "parquet_size_stat_failed",
                path=str(path),
                error=str(exc),
            )
    return total


# ---------------------------------------------------------------------------
# Internal: figure out which tables are still referenced by surviving candidates
# ---------------------------------------------------------------------------


def _surviving_table_ids(engine: "Engine") -> set[int]:
    """
    Return the set of table_ids that appear on EITHER side of any row in
    fk_candidates.  These are the tables we MUST keep Parquet for, because
    downstream phases (5 = validate) read them.
    """
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy import union  # noqa: PLC0415

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
    return {int(r[0]) for r in rows}


def _table_id_to_parquet_path(engine: "Engine") -> dict[int, str]:
    """
    Return ``{table_id: parquet_path}`` for every tbl_inventory row that has a
    populated parquet_path.  Used to translate "delete table X" into "delete
    file Y".
    """
    from sqlalchemy import select  # noqa: PLC0415

    from discovery.results_db import tbl_inventory_t  # noqa: PLC0415

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                tbl_inventory_t.c.table_id,
                tbl_inventory_t.c.parquet_path,
                tbl_inventory_t.c.status,
            ).where(tbl_inventory_t.c.parquet_path.is_not(None))
        ).all()
    return {int(r[0]): str(r[1]) for r in rows}


def _candidate_tables_for_gc(engine: "Engine") -> list[dict[str, Any]]:
    """
    Return rows for tables that *might* be candidates for orphan GC.  These are
    rows in tbl_inventory with status in {'extracted', 'excluded'} that have a
    populated parquet_path.

    Caller filters further by intersecting against the survivor set.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from discovery.results_db import tbl_inventory_t  # noqa: PLC0415

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                tbl_inventory_t.c.table_id,
                tbl_inventory_t.c.schema_name,
                tbl_inventory_t.c.table_name,
                tbl_inventory_t.c.parquet_path,
                tbl_inventory_t.c.parquet_bytes,
                tbl_inventory_t.c.status,
            )
            .where(
                tbl_inventory_t.c.parquet_path.is_not(None),
                tbl_inventory_t.c.status.in_(["extracted", "excluded"]),
            )
        ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Public GC entry point
# ---------------------------------------------------------------------------


def gc_orphaned_parquet(
    engine: "Engine",
    config: Any,
    dry_run: bool = False,
) -> list[Path]:
    """
    Delete Parquet files for tables that no surviving fk_candidate references.

    Conservative rule
    -----------------
    A table's Parquet file is deletable iff:

    * ``tbl_inventory.status`` is ``'extracted'`` or ``'excluded'`` (the only
      states under which a parquet_path could legitimately be populated), AND
    * ``table_id`` does NOT appear on either side of any row in
      ``fk_candidates``.

    Tables that survive Phase 4 candidate generation are required by Phase 5
    validate (and re-extracted by the two-pass orchestrator), so they MUST
    be kept.  Excluded tables shouldn't have Parquet files at all, but if
    one is found we treat it as an orphan.

    Parameters
    ----------
    engine:
        SQLAlchemy engine connected to the discovery results DB.
    config:
        AppConfig (only ``storage.base_path`` is read).
    dry_run:
        If True, log what would be deleted but don't unlink anything.

    Returns
    -------
    list[Path]
        Paths that were deleted (or would have been, in dry_run mode).
    """
    survivors = _surviving_table_ids(engine)
    candidates = _candidate_tables_for_gc(engine)

    log.info(
        "gc_scan_start",
        survivor_count=len(survivors),
        candidate_count=len(candidates),
        dry_run=dry_run,
    )

    deleted: list[Path] = []
    bytes_freed = 0
    base = Path(config.storage.base_path)

    for row in candidates:
        table_id = int(row["table_id"])
        if table_id in survivors:
            continue

        path_str = row["parquet_path"]
        if not path_str:
            continue
        path = Path(path_str)

        # Defensive: only delete files actually under storage.base_path so a
        # mis-recorded absolute path can't unlink arbitrary files.
        try:
            path_resolved = path.resolve()
            base_resolved = base.resolve()
        except OSError:
            log.warning(
                "gc_path_resolve_failed",
                path=str(path),
                table_id=table_id,
            )
            continue

        try:
            path_resolved.relative_to(base_resolved)
        except ValueError:
            log.warning(
                "gc_skip_outside_base",
                path=str(path_resolved),
                base_path=str(base_resolved),
                table_id=table_id,
            )
            continue

        size = 0
        if path.exists():
            try:
                size = path.stat().st_size
            except OSError:
                size = 0

        if dry_run:
            log.info(
                "gc_would_delete",
                schema=row["schema_name"],
                table=row["table_name"],
                table_id=table_id,
                path=str(path),
                bytes=size,
                status=row["status"],
            )
            deleted.append(path)
            bytes_freed += size
            continue

        if path.exists():
            try:
                path.unlink()
                log.info(
                    "gc_deleted",
                    schema=row["schema_name"],
                    table=row["table_name"],
                    table_id=table_id,
                    path=str(path),
                    bytes=size,
                    status=row["status"],
                )
                deleted.append(path)
                bytes_freed += size
            except OSError as exc:
                log.warning(
                    "gc_delete_failed",
                    path=str(path),
                    table_id=table_id,
                    error=str(exc),
                )
        else:
            # File already gone — record the orphan record but don't count bytes
            log.debug(
                "gc_path_missing",
                table_id=table_id,
                path=str(path),
            )

    log.info(
        "gc_scan_complete",
        deleted_count=len(deleted),
        bytes_freed=bytes_freed,
        dry_run=dry_run,
    )
    return deleted


# ---------------------------------------------------------------------------
# Disk cap enforcement
# ---------------------------------------------------------------------------


def enforce_disk_cap(
    config: Any,
    soft_cap_bytes: int | None = None,
    engine: "Engine | None" = None,
) -> bool:
    """
    Check current Parquet usage versus the soft cap; trigger orphan GC if
    over and warn on residual pressure.

    Non-blocking by design: never raises, always returns a bool.

    Parameters
    ----------
    config:
        AppConfig.  ``storage.base_path`` and ``storage.parquet_cap_bytes``
        are read.
    soft_cap_bytes:
        Override for ``config.storage.parquet_cap_bytes`` (useful for tests).
    engine:
        Optional engine for orphan GC.  If omitted and we exceed the cap,
        we log a warning and skip the GC step (i.e. the orchestrator
        provides one; the standalone ``cleanup`` CLI also passes one).

    Returns
    -------
    bool
        True if usage stayed within cap (or returned within cap after GC);
        False if we are STILL over after best-effort GC.
    """
    cap = soft_cap_bytes
    if cap is None:
        cap = int(getattr(config.storage, "parquet_cap_bytes", 0) or 0)

    used = current_parquet_bytes(config)

    if cap <= 0:
        log.debug("disk_cap_unset_or_zero", used_bytes=used)
        return True

    if used <= cap:
        log.debug("disk_cap_ok", used_bytes=used, cap_bytes=cap)
        return True

    log.warning(
        "disk_cap_exceeded",
        used_bytes=used,
        cap_bytes=cap,
        over_by_bytes=used - cap,
    )

    if engine is None:
        log.warning(
            "disk_cap_no_engine_for_gc",
            used_bytes=used,
            cap_bytes=cap,
        )
        return False

    deleted = gc_orphaned_parquet(engine, config, dry_run=False)
    used_after = current_parquet_bytes(config)

    if used_after <= cap:
        log.info(
            "disk_cap_recovered_after_gc",
            deleted_count=len(deleted),
            used_bytes=used_after,
            cap_bytes=cap,
        )
        return True

    log.warning(
        "disk_cap_still_exceeded_after_gc",
        deleted_count=len(deleted),
        used_bytes=used_after,
        cap_bytes=cap,
        over_by_bytes=used_after - cap,
    )
    return False
