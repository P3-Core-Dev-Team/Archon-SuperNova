"""
test_run_log.py — RunLog integration tests against ephemeral Postgres.

Assumes conftest.py (another agent) provides:
  - engine: SQLAlchemy Engine connected to a test Postgres instance
    with the discovery schema already initialized.

Tests:
  - start() creates a row with status='started'
  - succeed() marks status='succeeded', ended_at set
  - fail() marks status='failed', error_message stored
  - restart: calling start() on a failed task resets to 'started'
  - is_complete() returns True for 'succeeded' or 'skipped'
  - progress() returns {status: count} aggregation
  - skip() marks status='skipped'
  - Global scope (scope_id=None → stored as 0) works correctly
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from discovery.results_db import run_log_t
from discovery.run_log import GLOBAL_SCOPE_ID, RunLog


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_row(engine: Engine, phase: str, scope_type: str, scope_id: int) -> dict:  # noqa: E501
    with engine.connect() as conn:
        row = conn.execute(
            select(run_log_t).where(
                run_log_t.c.phase == phase,
                run_log_t.c.scope_type == scope_type,
                run_log_t.c.scope_id == scope_id,
            )
        ).mappings().first()
    return dict(row) if row else {}


def _clear_run_log(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM discovery.run_log"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunLogStart:
    def test_start_creates_row(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("inventory", "global", None)

        row = _fetch_row(engine, "inventory", "global", GLOBAL_SCOPE_ID)
        assert row["status"] == "started"
        assert row["started_at"] is not None
        assert row["ended_at"] is None
        assert row["error_message"] is None

    def test_start_with_metadata(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("extract", "table", 42, metadata={"table_name": "customers"})

        row = _fetch_row(engine, "extract", "table", 42)
        assert row["status"] == "started"
        assert row["metadata"] is not None
        assert row["metadata"]["table_name"] == "customers"


class TestRunLogSucceed:
    def test_succeed_marks_completed(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("extract", "table", 1)
        rl.succeed("extract", "table", 1)

        row = _fetch_row(engine, "extract", "table", 1)
        assert row["status"] == "succeeded"
        assert row["ended_at"] is not None
        assert row["error_message"] is None


class TestRunLogFail:
    def test_fail_stores_error(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("extract", "table", 2)
        rl.fail("extract", "table", 2, "Connection timed out after 7200s")

        row = _fetch_row(engine, "extract", "table", 2)
        assert row["status"] == "failed"
        assert "timed out" in row["error_message"]
        assert row["ended_at"] is not None


class TestRunLogRestart:
    def test_restart_failed_task(self, engine: Engine) -> None:
        """Calling start() on a failed task should reset it to 'started'."""
        _clear_run_log(engine)
        rl = RunLog(engine)

        # First attempt — fails
        rl.start("extract", "table", 3)
        rl.fail("extract", "table", 3, "Disk full")

        row = _fetch_row(engine, "extract", "table", 3)
        assert row["status"] == "failed"

        # Restart
        rl.start("extract", "table", 3)

        row = _fetch_row(engine, "extract", "table", 3)
        assert row["status"] == "started"
        assert row["ended_at"] is None
        assert row["error_message"] is None

    def test_restart_preserves_uniqueness(self, engine: Engine) -> None:
        """There should be exactly one row after restart (not two rows)."""
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("extract", "table", 4)
        rl.fail("extract", "table", 4, "Error 1")
        rl.start("extract", "table", 4)  # restart

        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM discovery.run_log "
                    "WHERE phase='extract' AND scope_type='table' AND scope_id=4"
                )
            ).scalar()
        assert count == 1, f"Expected 1 row after restart, got {count}"


class TestRunLogIsComplete:
    def test_is_complete_false_when_no_row(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        assert rl.is_complete("extract", "table", 999) is False

    def test_is_complete_false_when_started(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("extract", "table", 5)
        assert rl.is_complete("extract", "table", 5) is False

    def test_is_complete_false_when_failed(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("extract", "table", 6)
        rl.fail("extract", "table", 6, "some error")
        assert rl.is_complete("extract", "table", 6) is False

    def test_is_complete_true_when_succeeded(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("extract", "table", 7)
        rl.succeed("extract", "table", 7)
        assert rl.is_complete("extract", "table", 7) is True

    def test_is_complete_true_when_skipped(self, engine: Engine) -> None:
        """Skipped tasks are considered complete — they should not be redone."""
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.skip("extract", "table", 8, "excluded table")
        assert rl.is_complete("extract", "table", 8) is True


class TestRunLogProgress:
    def test_progress_empty(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        result = rl.progress("fingerprint")
        assert result == {}

    def test_progress_aggregation(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        # Insert various states for the 'extract' phase
        rl.start("extract", "table", 10)
        rl.succeed("extract", "table", 10)

        rl.start("extract", "table", 11)
        rl.succeed("extract", "table", 11)

        rl.start("extract", "table", 12)
        rl.fail("extract", "table", 12, "error")

        rl.start("extract", "table", 13)
        # left as 'started'

        result = rl.progress("extract")
        assert result.get("succeeded") == 2
        assert result.get("failed") == 1
        assert result.get("started") == 1

    def test_progress_isolated_by_phase(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("extract", "table", 20)
        rl.succeed("extract", "table", 20)

        rl.start("fingerprint", "table", 20)

        extract_progress = rl.progress("extract")
        fingerprint_progress = rl.progress("fingerprint")

        assert extract_progress.get("succeeded") == 1
        assert "started" not in extract_progress or extract_progress.get("started", 0) == 0

        assert fingerprint_progress.get("started") == 1
        assert "succeeded" not in fingerprint_progress or fingerprint_progress.get("succeeded", 0) == 0


class TestRunLogGlobalScope:
    def test_global_scope_uses_sentinel(self, engine: Engine) -> None:
        """scope_id=None stores as 0 in the DB."""
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("inventory", "global", None)

        row = _fetch_row(engine, "inventory", "global", GLOBAL_SCOPE_ID)
        assert row["scope_id"] == 0

    def test_global_scope_uniqueness(self, engine: Engine) -> None:
        """Only one row allowed for (inventory, global, 0)."""
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.start("inventory", "global", None)
        rl.start("inventory", "global", None)  # second call — should upsert

        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM discovery.run_log "
                    "WHERE phase='inventory' AND scope_type='global'"
                )
            ).scalar()
        assert count == 1


class TestRunLogSkip:
    def test_skip_marks_skipped(self, engine: Engine) -> None:
        _clear_run_log(engine)
        rl = RunLog(engine)

        rl.skip("extract", "table", 30, "excluded by pattern")

        row = _fetch_row(engine, "extract", "table", 30)
        assert row["status"] == "skipped"
        assert "excluded by pattern" in row["error_message"]
        assert row["ended_at"] is not None
