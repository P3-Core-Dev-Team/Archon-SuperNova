"""
test_results_db_unexclude.py — Tests for ``TblInventory.unexclude``.

Background (E4): The standard upsert path deliberately omits ``status`` from
the update column set so that Phase 2 (extraction) "owns" the lifecycle.
Without a dedicated un-exclude path, removing a regex pattern from the
exclusion list and re-running inventory would leave the row stuck at
``status='excluded'``, and Phase 2 would silently skip it.

These tests verify:

* Calling ``unexclude`` on an excluded row resets all the state that should
  no longer apply (status, exclusion_reason, parquet_*, extracted_at) and
  returns ``True``.
* Calling ``unexclude`` on a row that is NOT excluded is a guarded no-op
  (returns ``False``) — Phase 2 progress must not be overwritten.
* A non-existent ``table_id`` returns ``False``.

These are integration tests; they require the testcontainers Postgres
fixture from ``conftest.py``.  They are skipped when Docker is unavailable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine

from discovery.results_db import TblInventory, tbl_inventory_t, txn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_excluded(engine: Engine, schema: str, table: str) -> int:
    """Insert a row in ``status='excluded'`` and return its table_id."""
    with txn(engine) as conn:
        dao = TblInventory(conn)
        dao.upsert(
            {
                "schema_name": schema,
                "table_name": table,
                "status": "excluded",
                "exclusion_reason": "matched test exclusion",
                "parquet_path": f"/tmp/{schema}__{table}.parquet",
                "parquet_bytes": 1234,
                "extracted_at": datetime.now(timezone.utc),
            }
        )
        row = dao.get_by_name(schema, table)
    assert row is not None, "failed to insert fixture row"
    return int(row["table_id"])


def _insert_extracted(engine: Engine, schema: str, table: str) -> int:
    """Insert a row that has progressed past 'pending' to 'extracted'."""
    with txn(engine) as conn:
        dao = TblInventory(conn)
        dao.upsert({"schema_name": schema, "table_name": table})
        # Move it forward via the proper Phase-2 path.
        dao.mark_extracted(
            schema_name=schema,
            table_name=table,
            parquet_path=f"/tmp/{schema}__{table}.parquet",
            parquet_bytes=999,
            row_count_estimate=10,
            extracted_at=datetime.now(timezone.utc),
        )
        row = dao.get_by_name(schema, table)
    assert row is not None
    return int(row["table_id"])


def _read(engine: Engine, table_id: int) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            select(tbl_inventory_t).where(tbl_inventory_t.c.table_id == table_id)
        ).mappings().first()
    assert row is not None, f"table_id={table_id} not found"
    return dict(row)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unexclude_excluded_row_returns_true_and_resets(engine: Engine) -> None:
    table_id = _insert_excluded(engine, "public", "test_unexclude_basic")

    with txn(engine) as conn:
        dao = TblInventory(conn)
        result = dao.unexclude(table_id)

    assert result is True

    row = _read(engine, table_id)
    assert row["status"] == "pending"
    assert row["exclusion_reason"] is None
    assert row["parquet_path"] is None
    assert row["parquet_bytes"] is None
    assert row["extracted_at"] is None


def test_unexclude_extracted_row_returns_false(engine: Engine) -> None:
    """Phase 2 progress must NOT be overwritten by a stray un-exclude call."""
    table_id = _insert_extracted(engine, "public", "test_unexclude_extracted")

    with txn(engine) as conn:
        dao = TblInventory(conn)
        result = dao.unexclude(table_id)

    assert result is False

    row = _read(engine, table_id)
    # Untouched: still 'extracted', parquet_path still set.
    assert row["status"] == "extracted"
    assert row["parquet_path"] is not None


def test_unexclude_nonexistent_id_returns_false(engine: Engine) -> None:
    with txn(engine) as conn:
        dao = TblInventory(conn)
        result = dao.unexclude(table_id=999_999_999)
    assert result is False


def test_unexclude_idempotent_second_call_is_noop(engine: Engine) -> None:
    """Calling twice: first updates, second returns False (already pending)."""
    table_id = _insert_excluded(engine, "public", "test_unexclude_idempotent")

    with txn(engine) as conn:
        dao = TblInventory(conn)
        first = dao.unexclude(table_id)
    with txn(engine) as conn:
        dao = TblInventory(conn)
        second = dao.unexclude(table_id)

    assert first is True
    assert second is False
    row = _read(engine, table_id)
    assert row["status"] == "pending"
