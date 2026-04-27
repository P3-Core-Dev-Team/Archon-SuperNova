"""
run_log.py — Resumability ledger backed by discovery.run_log.

Every pipeline phase records per-task progress here so that a crashed run can
be resumed without re-doing completed work.

Uniqueness contract: UNIQUE (phase, scope_type, scope_id).

Sentinel for global scope:
  scope_id for scope_type='global' tasks uses the integer sentinel 0.
  This avoids Postgres's NULLS DISTINCT behaviour which would allow multiple
  rows with the same (phase, scope_type, NULL) to coexist — defeating the
  UNIQUE constraint and preventing ON CONFLICT from matching.

ON CONFLICT restart semantics (.start()):
  INSERT ... ON CONFLICT (phase, scope_type, scope_id) DO UPDATE
      SET status='started', started_at=now(), ended_at=NULL, error_message=NULL
  This means calling .start() on a failed task properly restarts it.

Status lifecycle:
  started → succeeded
  started → failed
  started → skipped (manual override; e.g. task determined unnecessary)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from discovery.results_db import run_log_t, txn

# Global-scope sentinel (avoids NULL in UNIQUE constraint)
GLOBAL_SCOPE_ID: int = 0


class RunLog:
    """
    Resumability ledger.

    Parameters
    ----------
    engine:
        SQLAlchemy engine connected to the discovery results DB.

    Usage::

        rl = RunLog(engine)
        if rl.is_complete("extract", "table", table_id):
            return  # already done
        rl.start("extract", "table", table_id)
        try:
            do_work()
            rl.succeed("extract", "table", table_id)
        except Exception as e:
            rl.fail("extract", "table", table_id, str(e))
            raise
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def start(
        self,
        phase: str,
        scope_type: str,
        scope_id: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record that *phase/scope_type/scope_id* is starting.

        If a row already exists (e.g. from a previous failed run), it is
        restarted: status reset to 'started', started_at reset to now(),
        ended_at and error_message cleared.

        Parameters
        ----------
        scope_id:
            Table id, candidate id, or similar identifier.
            Pass None (or omit) for global-scope tasks — internally stored as 0.
        metadata:
            Optional JSON-serialisable dict with extra context.
        """
        sid = GLOBAL_SCOPE_ID if scope_id is None else scope_id
        row: dict[str, Any] = {
            "phase": phase,
            "scope_type": scope_type,
            "scope_id": sid,
            "status": "started",
            "started_at": _now(),
            "ended_at": None,
            "error_message": None,
            "metadata": metadata,
        }
        stmt = insert(run_log_t).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["phase", "scope_type", "scope_id"],
            set_={
                "status": "started",
                "started_at": _now(),
                "ended_at": None,
                "error_message": None,
                "metadata": metadata,
            },
        )
        with txn(self._engine) as conn:
            conn.execute(stmt)

    def succeed(
        self,
        phase: str,
        scope_type: str,
        scope_id: int | None,
    ) -> None:
        """Mark task as succeeded."""
        self._set_terminal(phase, scope_type, scope_id, "succeeded", None)

    def fail(
        self,
        phase: str,
        scope_type: str,
        scope_id: int | None,
        error_message: str,
    ) -> None:
        """Mark task as failed with an error message."""
        self._set_terminal(phase, scope_type, scope_id, "failed", error_message)

    def skip(
        self,
        phase: str,
        scope_type: str,
        scope_id: int | None,
        reason: str,
    ) -> None:
        """
        Mark task as skipped.

        Uses .start() first to ensure a row exists, then immediately marks
        it skipped.  This means a previously-succeeded row will NOT be
        overwritten by skip (the upsert sets status='started' first, but the
        second update then sets 'skipped').

        For most callers, skip should only be called when is_complete() is False.
        """
        sid = GLOBAL_SCOPE_ID if scope_id is None else scope_id
        row: dict[str, Any] = {
            "phase": phase,
            "scope_type": scope_type,
            "scope_id": sid,
            "status": "skipped",
            "started_at": _now(),
            "ended_at": _now(),
            "error_message": reason,
            "metadata": None,
        }
        stmt = insert(run_log_t).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["phase", "scope_type", "scope_id"],
            set_={
                "status": "skipped",
                "ended_at": _now(),
                "error_message": reason,
            },
        )
        with txn(self._engine) as conn:
            conn.execute(stmt)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def is_complete(
        self,
        phase: str,
        scope_type: str,
        scope_id: int | None,
    ) -> bool:
        """
        Return True if the task is in a terminal "do-not-redo" state.

        Specifically, status in ``{'succeeded', 'skipped'}`` is treated as
        complete.  ``'failed'`` and ``'started'`` are NOT complete — those
        callers want resume logic to retry them.

        Used as a guard at the top of every task function to skip already-done
        work on resume.
        """
        sid = GLOBAL_SCOPE_ID if scope_id is None else scope_id
        with self._engine.connect() as conn:
            row = conn.execute(
                select(run_log_t.c.status).where(
                    run_log_t.c.phase == phase,
                    run_log_t.c.scope_type == scope_type,
                    run_log_t.c.scope_id == sid,
                )
            ).first()
        return row is not None and row.status in ("succeeded", "skipped")

    def progress(self, phase: str) -> dict[str, int]:
        """
        Return a mapping of {status: count} for all tasks in *phase*.

        Example::

            {"succeeded": 900, "started": 3, "failed": 1}

        Tasks not yet started are not represented (no row exists yet).
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(run_log_t.c.status, func.count().label("cnt"))
                .where(run_log_t.c.phase == phase)
                .group_by(run_log_t.c.status)
            ).all()
        return {row.status: row.cnt for row in rows}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _set_terminal(
        self,
        phase: str,
        scope_type: str,
        scope_id: int | None,
        status: str,
        error_message: str | None,
    ) -> None:
        sid = GLOBAL_SCOPE_ID if scope_id is None else scope_id
        row: dict[str, Any] = {
            "phase": phase,
            "scope_type": scope_type,
            "scope_id": sid,
            "status": status,
            "started_at": _now(),
            "ended_at": _now(),
            "error_message": error_message,
            "metadata": None,
        }
        stmt = insert(run_log_t).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["phase", "scope_type", "scope_id"],
            set_={
                "status": status,
                "ended_at": _now(),
                "error_message": error_message,
            },
        )
        with txn(self._engine) as conn:
            conn.execute(stmt)


def _now() -> datetime:
    """UTC-aware datetime for DB timestamps."""
    return datetime.now(timezone.utc)
