"""
Phase 3a — Column fingerprinting.

This module owns BOTH the pure helpers (``fingerprint_column``,
``ColumnFingerprint``) and the Phase 3a orchestrator (``run_phase_3a``).

Pure helpers have no SQLAlchemy / config / run_log imports.  The orchestrator
imports those at function-scope so this file can be imported in test contexts
that don't need the full DAO surface.

Exports
-------
ColumnFingerprint   dataclass returned by fingerprint_column()
fingerprint_column  pure entry point — fingerprint a single Parquet column
run_phase_3a        coordinator: read pending columns, dispatch, persist
"""
from __future__ import annotations

import multiprocessing
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import pyarrow.parquet as pq
import structlog
import xxhash

# ---------------------------------------------------------------------------
# HyperLogLog++ (always available in datasketch)
# ---------------------------------------------------------------------------
from datasketch import HyperLogLogPlusPlus

# ---------------------------------------------------------------------------
# HyperMinHash — 1.6.5+ has it; older datasketch may not.
# Fall back to MinHash when unavailable.
# ---------------------------------------------------------------------------
try:
    from datasketch import HyperMinHash as _HyperMinHash  # type: ignore[attr-defined]
    _HYPERMINHASH_AVAILABLE = True
except (ImportError, AttributeError):
    _HYPERMINHASH_AVAILABLE = False

from datasketch import MinHash

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from discovery.config import AppConfig

log = structlog.get_logger("discovery.fingerprint")

# HyperMinHash supports specific power-of-two num_buckets values.  Anything
# outside this set is rejected (and we fall back to the default).
_HMH_VALID_NUM_BUCKETS = frozenset({64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384})
_HMH_DEFAULT_NUM_BUCKETS = 1024
_HMH_DEFAULT_BITS_PER_BUCKET = 8


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class ColumnFingerprint:
    """Result of fingerprinting one column of one Parquet file."""

    parquet_path: str
    column: str
    cardinality_estimate: int
    cardinality_method: str  # 'hll++' | 'exact' | 'hyperminhash'
    sketcher_kind: str       # 'hyperminhash' | 'minhash'
    null_count: int
    row_count: int
    min_val: Optional[str]
    max_val: Optional[str]
    null_pct: float
    sketch_blob: bytes
    # Observability for adaptive HLL early-stop (C3).  These fields are
    # informational only — they are NOT persisted to col_inventory.
    early_stopped: bool = False
    row_groups_read: int = 0
    row_groups_total: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_sketcher(
    sketcher: str,
    num_perm: int,
    num_buckets: int = _HMH_DEFAULT_NUM_BUCKETS,
    bits_per_bucket: int = _HMH_DEFAULT_BITS_PER_BUCKET,
) -> object:
    """Return a fresh sketch object (HyperMinHash or MinHash).

    HyperMinHash only supports a fixed set of power-of-two ``num_buckets``
    values (see ``_HMH_VALID_NUM_BUCKETS``).  Out-of-range values fall back
    to the safe default of 1024.
    """
    if sketcher == "hyperminhash" and _HYPERMINHASH_AVAILABLE:
        if num_buckets not in _HMH_VALID_NUM_BUCKETS:
            log.warning(
                "fingerprint.hmh_invalid_num_buckets",
                requested=num_buckets,
                fallback=_HMH_DEFAULT_NUM_BUCKETS,
            )
            num_buckets = _HMH_DEFAULT_NUM_BUCKETS
        return _HyperMinHash(num_buckets=num_buckets, bits_per_bucket=bits_per_bucket)
    return MinHash(num_perm=num_perm)


def _update_sketch(sk: object, hash_bytes: bytes) -> None:
    """Feed a hashed value into any supported sketch type."""
    sk.update(hash_bytes)


def _sketcher_kind_name(sketcher: str) -> str:
    if sketcher == "hyperminhash" and _HYPERMINHASH_AVAILABLE:
        return "hyperminhash"
    return "minhash"


def _exact_distinct(parquet_path: Path, column: str) -> int:
    """Second pass: exact COUNT(DISTINCT) via DuckDB (avoids loading values into RAM)."""
    try:
        import duckdb  # noqa: PLC0415
        con = duckdb.connect()
        result = con.execute(
            f"SELECT COUNT(DISTINCT \"{column}\") FROM read_parquet('{parquet_path}')"
        ).fetchone()
        con.close()
        return int(result[0]) if result else 0
    except FileNotFoundError:
        log.warning(
            "fingerprint.exact_distinct.not_found",
            parquet_path=str(parquet_path),
            column=column,
        )
        return 0
    except Exception as exc:
        # DuckDB unavailable or path error — return 0 so caller keeps HLL estimate.
        log.warning(
            "fingerprint.exact_distinct.failed",
            parquet_path=str(parquet_path),
            column=column,
            error=str(exc),
        )
        return 0


# ---------------------------------------------------------------------------
# Public pure API
# ---------------------------------------------------------------------------


def fingerprint_column(
    parquet_path: Path,
    column: str,
    sketcher: str = "hyperminhash",
    num_perm: int = 256,
    hll_p: int = 14,
    exact_distinct_below: int = 10_000,
    num_buckets: int = _HMH_DEFAULT_NUM_BUCKETS,
    bits_per_bucket: int = _HMH_DEFAULT_BITS_PER_BUCKET,
    early_stop_delta: float = 0.005,
) -> ColumnFingerprint:
    """
    Stream a Parquet column through row groups; build sketch + HLL cardinality.

    Adaptive early-stop (C3)
    ------------------------
    After at least 3 row groups have been read, if the relative HLL
    cardinality delta drops below ``early_stop_delta`` for two *consecutive*
    row groups, stop reading further row groups.  This is recall-safe: when
    HLL has stabilised, no new distinct values are arriving — therefore the
    MinHash / HyperMinHash signature has also stabilised — so additional
    row-group reads cannot materially change the result.
    """
    parquet_path = Path(parquet_path)
    sk = _make_sketcher(sketcher, num_perm, num_buckets, bits_per_bucket)
    hll = HyperLogLogPlusPlus(p=hll_p)

    null_count = 0
    row_count = 0
    # Track min/max on the raw value (numeric/date/string) so integer columns
    # don't get lexicographic min/max ("9999" > "10000" → False positives in
    # range-overlap checks downstream).  Stringified at the end for storage.
    min_raw: object = None
    max_raw: object = None

    pf = pq.ParquetFile(str(parquet_path))
    total_row_groups = pf.num_row_groups

    # Adaptive early-stop bookkeeping.
    prev_card: int = -1
    stable_streak: int = 0
    early_stopped: bool = False
    row_groups_read: int = 0

    for rg_idx in range(total_row_groups):
        batch = pf.read_row_group(rg_idx, columns=[column])
        col_array = batch.column(column)
        for val in col_array.to_pylist():
            row_count += 1
            if val is None:
                null_count += 1
                continue
            s = str(val)
            h_int = xxhash.xxh3_64_intdigest(s.encode("utf-8"))
            h_bytes = h_int.to_bytes(8, "big")
            _update_sketch(sk, h_bytes)
            hll.update(h_bytes)
            try:
                if min_raw is None or val < min_raw:  # type: ignore[operator]
                    min_raw = val
                if max_raw is None or val > max_raw:  # type: ignore[operator]
                    max_raw = val
            except TypeError:
                # Mixed types in a column (rare): fall back to string compare.
                if min_raw is None or s < str(min_raw):
                    min_raw = val
                if max_raw is None or s > str(max_raw):
                    max_raw = val

        row_groups_read = rg_idx + 1

        # C3: adaptive early-stop.  Compute the post-row-group HLL estimate
        # every iteration; track a "stable streak" of consecutive row groups
        # whose relative delta vs the previous estimate is below the
        # threshold.  Allow the break only after ≥3 row groups have been read
        # (i.e. rg_idx >= 2) AND two consecutive sub-threshold deltas.
        if early_stop_delta > 0:
            cur_card = int(hll.count())
            if prev_card >= 0:
                delta = abs(cur_card - prev_card) / max(prev_card, 1)
                if delta < early_stop_delta:
                    stable_streak += 1
                else:
                    stable_streak = 0
            prev_card = cur_card

            if rg_idx >= 2 and stable_streak >= 2:
                log.debug(
                    "fingerprint.early_stop",
                    column=column,
                    parquet_path=str(parquet_path),
                    rg_idx=rg_idx,
                    row_groups_read=row_groups_read,
                    row_groups_total=total_row_groups,
                    cardinality_estimate=cur_card,
                )
                early_stopped = True
                break

    cardinality_estimate = int(hll.count())
    cardinality_method = "hll++"
    sketcher_kind = _sketcher_kind_name(sketcher)

    if cardinality_estimate < exact_distinct_below:
        exact = _exact_distinct(parquet_path, column)
        if exact > 0:
            cardinality_estimate = exact
            cardinality_method = "exact"

    null_pct = (null_count / row_count) if row_count > 0 else 0.0

    min_val = None if min_raw is None else str(min_raw)
    max_val = None if max_raw is None else str(max_raw)

    return ColumnFingerprint(
        parquet_path=str(parquet_path),
        column=column,
        cardinality_estimate=cardinality_estimate,
        cardinality_method=cardinality_method,
        sketcher_kind=sketcher_kind,
        null_count=null_count,
        row_count=row_count,
        min_val=min_val,
        max_val=max_val,
        null_pct=null_pct,
        sketch_blob=pickle.dumps(sk),
        early_stopped=early_stopped,
        row_groups_read=row_groups_read,
        row_groups_total=total_row_groups,
    )


# ---------------------------------------------------------------------------
# Worker pool (Phase 3a)
# ---------------------------------------------------------------------------

_worker_config: dict[str, Any] = {}


def _worker_init(config_dict: dict) -> None:
    """Called once per worker process to cache settings."""
    global _worker_config
    _worker_config = config_dict


def _fingerprint_task(args: tuple) -> dict | None:
    """
    Picklable worker task for multiprocessing.Pool.

    args: (column_id, parquet_path, column_name)
    Returns a dict of col_inventory fields to update, or None on error.
    """
    column_id, parquet_path, column_name = args
    cfg = _worker_config

    try:
        fp = fingerprint_column(
            parquet_path=Path(parquet_path),
            column=column_name,
            sketcher=cfg.get("sketcher", "hyperminhash"),
            num_perm=cfg.get("num_perm", 256),
            hll_p=cfg.get("hll_p", 14),
            exact_distinct_below=cfg.get("exact_distinct_below", 10_000),
            num_buckets=cfg.get("num_buckets", _HMH_DEFAULT_NUM_BUCKETS),
            bits_per_bucket=cfg.get("bits_per_bucket", _HMH_DEFAULT_BITS_PER_BUCKET),
            early_stop_delta=cfg.get("early_stop_delta", 0.005),
        )
        return {
            "column_id": column_id,
            "sketch_blob": fp.sketch_blob,
            "distinct_count": fp.cardinality_estimate,
            "cardinality_estimate": fp.cardinality_estimate,
            "null_pct": fp.null_pct,
            "cardinality_method": fp.cardinality_method,
            "sketcher_kind": fp.sketcher_kind,
            "min_val": fp.min_val,
            "max_val": fp.max_val,
            # Observability — not persisted to col_inventory.
            "early_stopped": fp.early_stopped,
            "row_groups_read": fp.row_groups_read,
            "row_groups_total": fp.row_groups_total,
        }
    except Exception as exc:
        log.error(
            "fingerprint.task_failed",
            column_id=column_id,
            parquet_path=str(parquet_path),
            column=column_name,
            error=str(exc),
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Phase 3a entry point
# ---------------------------------------------------------------------------

# Batch size for grouped persistence transactions (rows per commit).
_PERSIST_BATCH = 500


def run_phase_3a(engine: "Engine", config: "AppConfig") -> None:
    """
    Orchestrate Phase 3a: fingerprint all un-processed columns.

    Reads pending columns from col_inventory (fingerprinted_at IS NULL),
    dispatches to a multiprocess Pool, writes results back via DAO.
    Persists in batches to avoid per-row transactions.
    """
    from sqlalchemy import and_, select

    from discovery.results_db import (
        ColInventory,
        col_inventory_t,
        tbl_inventory_t,
        txn,
    )
    from discovery.run_log import RunLog

    run_log = RunLog(engine)

    # --- Read pending columns (fingerprinted_at IS NULL, table extracted) ---
    # C4: Skip non-FK-eligible columns (STRING_LONG / BINARY / FLOAT / BOOL)
    # at the SQL layer.  Phase 4 would discard them anyway; fingerprinting
    # them wastes CPU and parquet IO.
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                col_inventory_t.c.column_id,
                col_inventory_t.c.column_name,
                col_inventory_t.c.table_id,
                col_inventory_t.c.type_class,
                col_inventory_t.c.is_fk_eligible,
                tbl_inventory_t.c.parquet_path,
            )
            .join(
                tbl_inventory_t,
                tbl_inventory_t.c.table_id == col_inventory_t.c.table_id,
            )
            .where(
                and_(
                    col_inventory_t.c.fingerprinted_at.is_(None),
                    col_inventory_t.c.is_fk_eligible.is_(True),
                    tbl_inventory_t.c.parquet_path.is_not(None),
                    tbl_inventory_t.c.status == "extracted",
                )
            )
        ).mappings().all()

    pending = list(rows)
    log.info("phase3a.pending_count", count=len(pending))
    if not pending:
        log.info("phase3a.nothing_to_do")
        return

    # Pre-build column_id → row map for downstream lookups
    column_to_row: dict[int, Any] = {row["column_id"]: row for row in pending}

    fp_cfg = getattr(config, "fingerprint", None)
    rel_cfg = getattr(config, "relationships", None)
    config_dict: dict = {
        "sketcher": getattr(fp_cfg, "sketcher", "hyperminhash"),
        "num_perm": getattr(rel_cfg, "lsh_num_perm", 256),
        "hll_p": getattr(fp_cfg, "hll_p", 14),
        "exact_distinct_below": getattr(fp_cfg, "exact_distinct_below", 10_000),
        # E8: wire the previously-hardcoded HyperMinHash knobs and the new
        # adaptive early-stop threshold from FingerprintConfig.
        "num_buckets": getattr(fp_cfg, "num_buckets", _HMH_DEFAULT_NUM_BUCKETS),
        "bits_per_bucket": getattr(
            fp_cfg, "bits_per_bucket", _HMH_DEFAULT_BITS_PER_BUCKET
        ),
        "hash_algorithm": getattr(fp_cfg, "hash_algorithm", "xxh3_64"),
        "early_stop_delta": getattr(fp_cfg, "early_stop_delta", 0.005),
    }

    tasks = [
        (row["column_id"], row["parquet_path"], row["column_name"])
        for row in pending
    ]

    orch_cfg = getattr(config, "orchestration", None)
    workers_cfg = getattr(orch_cfg, "workers", None)
    num_workers: int = getattr(workers_cfg, "fingerprint", 16)

    log.info(
        "phase3a.launching_pool",
        num_workers=num_workers,
        num_tasks=len(tasks),
    )

    with multiprocessing.Pool(
        processes=num_workers,
        initializer=_worker_init,
        initargs=(config_dict,),
    ) as pool:
        results = pool.map(_fingerprint_task, tasks)

    now = datetime.now(timezone.utc)
    success = failed = 0

    # Batch persistence: open one transaction per N successful results.
    pending_writes: list[tuple[int, dict[str, Any]]] = []

    def _flush() -> None:
        nonlocal success
        if not pending_writes:
            return
        with txn(engine) as conn:
            col_dao = ColInventory(conn)
            for _column_id, payload in pending_writes:
                col_dao.update_fingerprint(payload)
        # Per-column run_log writes happen outside the transaction (run_log
        # uses its own engine.begin() and is idempotent).
        for column_id, _payload in pending_writes:
            run_log.succeed("fingerprint", "column", column_id)
        success += len(pending_writes)
        pending_writes.clear()

    early_stop_count = 0
    for task_args, result in zip(tasks, results):
        column_id = task_args[0]
        if result is None:
            failed += 1
            run_log.fail(
                "fingerprint", "column", column_id, "fingerprint_task returned None"
            )
            continue
        if result.get("early_stopped"):
            early_stop_count += 1
        original = column_to_row[column_id]
        # Keep payload limited to columns owned by Phase 3a in col_inventory.
        # Observability fields (early_stopped, row_groups_read, row_groups_total)
        # are intentionally NOT persisted — col_inventory has no matching column.
        # The C3 agent owns physical_type via extraction.py; we never send it.
        payload: dict[str, Any] = {
            "table_id": original["table_id"],
            "column_name": task_args[2],
            "sketch_blob": result["sketch_blob"],
            "distinct_count": result["distinct_count"],
            "cardinality_estimate": result["cardinality_estimate"],
            "null_pct": result["null_pct"],
            "cardinality_method": result["cardinality_method"],
            "sketcher_kind": result["sketcher_kind"],
            "min_val": result["min_val"],
            "max_val": result["max_val"],
            "fingerprinted_at": now,
        }
        pending_writes.append((column_id, payload))

        if len(pending_writes) >= _PERSIST_BATCH:
            try:
                _flush()
            except Exception as exc:
                log.error(
                    "fingerprint.batch_flush_failed",
                    error=str(exc),
                    batch_size=len(pending_writes),
                    exc_info=True,
                )
                # Mark each pending write as failed in run_log.
                for column_id, _ in pending_writes:
                    run_log.fail("fingerprint", "column", column_id, str(exc))
                failed += len(pending_writes)
                pending_writes.clear()

    # Flush any remaining
    try:
        _flush()
    except Exception as exc:
        log.error(
            "fingerprint.final_flush_failed",
            error=str(exc),
            batch_size=len(pending_writes),
            exc_info=True,
        )
        for column_id, _ in pending_writes:
            run_log.fail("fingerprint", "column", column_id, str(exc))
        failed += len(pending_writes)
        pending_writes.clear()

    log.info(
        "phase3a.complete",
        success=success,
        failed=failed,
        early_stopped=early_stop_count,
    )
