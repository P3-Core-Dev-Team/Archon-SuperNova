"""
test_cleanup.py — Unit + integration tests for cleanup.py.

Strategy
--------
* Unit tests use mocked engines + on-disk Parquet fixtures to exercise the
  pure-Python branches of ``current_parquet_bytes``, ``enforce_disk_cap``,
  and the dry_run path of ``gc_orphaned_parquet`` (where DB read shape can
  be stubbed at the SQLAlchemy execute() boundary).

* Integration tests spin up an ephemeral Postgres via testcontainers and
  exercise the live SQL queries.  These are gated on docker/testcontainers
  availability and marked ``integration``.

Both layers verify the same conservative GC rule: tables on EITHER side of
a surviving fk_candidate keep their Parquet; tables in neither side and
status='extracted' are deleted.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from discovery import cleanup

# ---------------------------------------------------------------------------
# Optional docker/testcontainers
# ---------------------------------------------------------------------------

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore

    HAS_TESTCONTAINERS = True
except ImportError:
    HAS_TESTCONTAINERS = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _touch(path: Path, payload: bytes = b"x" * 100) -> None:
    """Create a parent dir + write a small payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


@pytest.fixture
def parquet_dir(tmp_path: Path) -> Path:
    base = tmp_path / "parquet"
    base.mkdir()
    return base


@pytest.fixture
def fake_config(parquet_dir: Path):
    """A minimal AppConfig stand-in exposing storage.{base_path,parquet_cap_bytes}."""
    storage = SimpleNamespace(
        base_path=str(parquet_dir),
        parquet_cap_bytes=1_000_000_000,
    )
    return SimpleNamespace(storage=storage)


# ---------------------------------------------------------------------------
# current_parquet_bytes
# ---------------------------------------------------------------------------


class TestCurrentParquetBytes:
    def test_empty_dir_returns_zero(self, fake_config):
        assert cleanup.current_parquet_bytes(fake_config) == 0

    def test_missing_dir_returns_zero(self, tmp_path):
        cfg = SimpleNamespace(
            storage=SimpleNamespace(
                base_path=str(tmp_path / "does-not-exist"),
                parquet_cap_bytes=10,
            )
        )
        assert cleanup.current_parquet_bytes(cfg) == 0

    def test_sums_files_recursively(self, fake_config, parquet_dir):
        _touch(parquet_dir / "a.parquet", b"a" * 100)
        _touch(parquet_dir / "sub" / "b.parquet", b"b" * 250)
        _touch(parquet_dir / "sub" / "c.txt", b"c" * 33)
        total = cleanup.current_parquet_bytes(fake_config)
        assert total == 100 + 250 + 33

    def test_ignores_directories(self, fake_config, parquet_dir):
        (parquet_dir / "empty_dir").mkdir()
        _touch(parquet_dir / "f.parquet", b"\x00" * 10)
        assert cleanup.current_parquet_bytes(fake_config) == 10


# ---------------------------------------------------------------------------
# gc_orphaned_parquet — engine-level mocking
# ---------------------------------------------------------------------------


def _mk_engine_with_rows(survivors_rows, candidate_rows):
    """
    Construct a MagicMock engine whose .connect().execute().all()/.mappings().all()
    returns the supplied row sets in order.

    cleanup.gc_orphaned_parquet calls execute() twice in two distinct contexts:
      1. _surviving_table_ids → engine.connect().execute(stmt).all()
         → list of (table_id,) rows
      2. _candidate_tables_for_gc → engine.connect().execute(stmt).mappings().all()
         → list of dict-like rows
    """
    engine = MagicMock(name="engine")

    # First .connect() returns a context manager whose execute returns a
    # mock with .all() yielding survivor rows.
    survivor_conn = MagicMock(name="survivor_conn")
    survivor_result = MagicMock(name="survivor_result")
    survivor_result.all.return_value = [(tid,) for tid in survivors_rows]
    survivor_conn.execute.return_value = survivor_result
    survivor_conn.__enter__ = MagicMock(return_value=survivor_conn)
    survivor_conn.__exit__ = MagicMock(return_value=None)

    candidate_conn = MagicMock(name="candidate_conn")
    candidate_result = MagicMock(name="candidate_result")
    candidate_mappings = MagicMock(name="candidate_mappings")
    candidate_mappings.all.return_value = candidate_rows
    candidate_result.mappings.return_value = candidate_mappings
    candidate_conn.execute.return_value = candidate_result
    candidate_conn.__enter__ = MagicMock(return_value=candidate_conn)
    candidate_conn.__exit__ = MagicMock(return_value=None)

    engine.connect.side_effect = [survivor_conn, candidate_conn]
    return engine


class TestGcOrphanedParquet:
    def test_deletes_orphaned_extracted_table(self, fake_config, parquet_dir):
        survivors = [10, 11]  # two surviving tables
        orphan_path = parquet_dir / "public__orphan.parquet"
        survivor_path = parquet_dir / "public__keeper.parquet"
        _touch(orphan_path, b"o" * 64)
        _touch(survivor_path, b"k" * 64)

        candidate_rows = [
            {
                "table_id": 99,
                "schema_name": "public",
                "table_name": "orphan",
                "parquet_path": str(orphan_path),
                "parquet_bytes": 64,
                "status": "extracted",
            },
            {
                "table_id": 10,
                "schema_name": "public",
                "table_name": "keeper",
                "parquet_path": str(survivor_path),
                "parquet_bytes": 64,
                "status": "extracted",
            },
        ]
        engine = _mk_engine_with_rows(survivors, candidate_rows)

        deleted = cleanup.gc_orphaned_parquet(engine, fake_config)

        assert orphan_path.exists() is False
        assert survivor_path.exists() is True
        assert len(deleted) == 1
        assert Path(str(deleted[0])).name == "public__orphan.parquet"

    def test_dry_run_does_not_delete(self, fake_config, parquet_dir):
        orphan_path = parquet_dir / "schema__t.parquet"
        _touch(orphan_path, b"x" * 32)

        engine = _mk_engine_with_rows(
            [],
            [
                {
                    "table_id": 7,
                    "schema_name": "schema",
                    "table_name": "t",
                    "parquet_path": str(orphan_path),
                    "parquet_bytes": 32,
                    "status": "extracted",
                }
            ],
        )

        deleted = cleanup.gc_orphaned_parquet(engine, fake_config, dry_run=True)

        assert orphan_path.exists() is True
        assert len(deleted) == 1

    def test_skips_paths_outside_base(self, fake_config, parquet_dir, tmp_path):
        outside = tmp_path / "outside.parquet"
        _touch(outside, b"o" * 16)

        engine = _mk_engine_with_rows(
            [],
            [
                {
                    "table_id": 5,
                    "schema_name": "x",
                    "table_name": "y",
                    "parquet_path": str(outside),
                    "parquet_bytes": 16,
                    "status": "extracted",
                }
            ],
        )

        deleted = cleanup.gc_orphaned_parquet(engine, fake_config)

        # Outside-base file must NOT be deleted.
        assert outside.exists() is True
        assert deleted == []

    def test_keeps_survivors(self, fake_config, parquet_dir):
        keep_path = parquet_dir / "public__order.parquet"
        _touch(keep_path, b"K" * 100)

        engine = _mk_engine_with_rows(
            [42],
            [
                {
                    "table_id": 42,
                    "schema_name": "public",
                    "table_name": "order",
                    "parquet_path": str(keep_path),
                    "parquet_bytes": 100,
                    "status": "extracted",
                }
            ],
        )

        deleted = cleanup.gc_orphaned_parquet(engine, fake_config)

        assert keep_path.exists() is True
        assert deleted == []

    def test_handles_missing_file(self, fake_config, parquet_dir):
        gone_path = parquet_dir / "ghost.parquet"
        # Note: do NOT create the file.

        engine = _mk_engine_with_rows(
            [],
            [
                {
                    "table_id": 8,
                    "schema_name": "g",
                    "table_name": "ghost",
                    "parquet_path": str(gone_path),
                    "parquet_bytes": 0,
                    "status": "extracted",
                }
            ],
        )

        # Should not raise — missing file is just a no-op.
        deleted = cleanup.gc_orphaned_parquet(engine, fake_config)

        assert deleted == []


# ---------------------------------------------------------------------------
# enforce_disk_cap
# ---------------------------------------------------------------------------


class TestEnforceDiskCap:
    def test_returns_true_when_under_cap(self, fake_config, parquet_dir):
        _touch(parquet_dir / "small.parquet", b"x" * 10)
        # cap is the default 1 GB, well above 10 bytes.
        assert cleanup.enforce_disk_cap(fake_config) is True

    def test_returns_true_when_cap_zero_or_unset(self, fake_config, parquet_dir):
        _touch(parquet_dir / "any.parquet", b"x" * 100)
        assert (
            cleanup.enforce_disk_cap(fake_config, soft_cap_bytes=0) is True
        )

    def test_warns_when_over_cap_no_engine(self, fake_config, parquet_dir):
        _touch(parquet_dir / "large.parquet", b"x" * 1000)
        # Use a 10-byte cap to force exceedence.
        result = cleanup.enforce_disk_cap(
            fake_config, soft_cap_bytes=10, engine=None
        )
        assert result is False

    def test_runs_gc_when_engine_supplied(self, fake_config, parquet_dir):
        # Drop two files: one orphan, one survivor.
        orphan = parquet_dir / "schema__o.parquet"
        keeper = parquet_dir / "schema__k.parquet"
        _touch(orphan, b"o" * 1000)
        _touch(keeper, b"k" * 1000)

        engine = _mk_engine_with_rows(
            [1],  # survivor
            [
                {
                    "table_id": 1,
                    "schema_name": "schema",
                    "table_name": "k",
                    "parquet_path": str(keeper),
                    "parquet_bytes": 1000,
                    "status": "extracted",
                },
                {
                    "table_id": 2,
                    "schema_name": "schema",
                    "table_name": "o",
                    "parquet_path": str(orphan),
                    "parquet_bytes": 1000,
                    "status": "extracted",
                },
            ],
        )

        # Cap = 1500: total = 2000, so we're over; after GC = 1000, under cap.
        result = cleanup.enforce_disk_cap(
            fake_config, soft_cap_bytes=1500, engine=engine
        )
        assert result is True
        assert orphan.exists() is False
        assert keeper.exists() is True


# ---------------------------------------------------------------------------
# Integration: real SQL queries against ephemeral Postgres
# ---------------------------------------------------------------------------


_SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS discovery;
SET search_path TO discovery;

CREATE TABLE IF NOT EXISTS tbl_inventory (
    table_id          BIGSERIAL PRIMARY KEY,
    schema_name       TEXT NOT NULL,
    table_name        TEXT NOT NULL,
    row_count_estimate BIGINT,
    byte_size_estimate BIGINT,
    status            TEXT NOT NULL DEFAULT 'pending',
    exclusion_reason  TEXT,
    parquet_path      TEXT,
    parquet_bytes     BIGINT,
    extracted_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS col_inventory (
    column_id         BIGSERIAL PRIMARY KEY,
    table_id          BIGINT NOT NULL REFERENCES tbl_inventory,
    column_name       TEXT NOT NULL,
    ordinal_position  INT NOT NULL,
    data_type         TEXT NOT NULL,
    type_class        TEXT NOT NULL,
    is_nullable       BOOLEAN NOT NULL,
    is_pk             BOOLEAN NOT NULL DEFAULT false,
    is_unique_indexed BOOLEAN NOT NULL DEFAULT false,
    is_indexed        BOOLEAN NOT NULL DEFAULT false,
    is_fk_eligible    BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (table_id, column_name)
);

CREATE TABLE IF NOT EXISTS fk_candidates (
    candidate_id      BIGSERIAL PRIMARY KEY,
    child_col_id      BIGINT NOT NULL REFERENCES col_inventory,
    parent_col_id     BIGINT NOT NULL REFERENCES col_inventory,
    estimated_containment REAL,
    name_similarity   REAL,
    type_match        BOOLEAN NOT NULL,
    source_stage      TEXT NOT NULL,
    joint_estimate    BIGINT,
    created_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (child_col_id, parent_col_id)
);
"""


@pytest.mark.integration
@pytest.mark.skipif(
    not HAS_TESTCONTAINERS, reason="testcontainers-python not installed"
)
class TestGcIntegration:
    @pytest.fixture(scope="class")
    def pg_engine(self):
        import sqlalchemy  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        try:
            with PostgresContainer("postgres:16") as pg:
                url = pg.get_connection_url()
                engine = sqlalchemy.create_engine(url, future=True)
                with engine.begin() as conn:
                    for stmt in _SCHEMA_DDL.split(";"):
                        s = stmt.strip()
                        if s:
                            conn.execute(text(s))
                yield engine
        except Exception as exc:
            pytest.skip(f"Cannot start Postgres container: {exc}")

    def test_orphan_deletion_uses_real_sql(self, pg_engine, tmp_path):
        """Populate a real schema, run gc_orphaned_parquet, assert correct files deleted."""
        from sqlalchemy import text  # noqa: PLC0415

        parquet_dir = tmp_path / "parquet"
        parquet_dir.mkdir()
        keeper = parquet_dir / "public__orders.parquet"
        orphan = parquet_dir / "public__legacy.parquet"
        _touch(keeper, b"o" * 200)
        _touch(orphan, b"l" * 100)

        with pg_engine.begin() as conn:
            # Two tables.
            orders_id = conn.execute(
                text(
                    "INSERT INTO discovery.tbl_inventory "
                    "(schema_name, table_name, status, parquet_path, parquet_bytes) "
                    "VALUES ('public', 'orders', 'extracted', :p, 200) "
                    "RETURNING table_id"
                ),
                {"p": str(keeper)},
            ).scalar()
            legacy_id = conn.execute(
                text(
                    "INSERT INTO discovery.tbl_inventory "
                    "(schema_name, table_name, status, parquet_path, parquet_bytes) "
                    "VALUES ('public', 'legacy', 'extracted', :p, 100) "
                    "RETURNING table_id"
                ),
                {"p": str(orphan)},
            ).scalar()

            # Two columns, one each.
            orders_col = conn.execute(
                text(
                    "INSERT INTO discovery.col_inventory "
                    "(table_id, column_name, ordinal_position, data_type, "
                    " type_class, is_nullable) "
                    "VALUES (:t, 'customer_id', 1, 'bigint', 'INT_WIDE', false) "
                    "RETURNING column_id"
                ),
                {"t": orders_id},
            ).scalar()
            customers_col = conn.execute(
                text(
                    "INSERT INTO discovery.col_inventory "
                    "(table_id, column_name, ordinal_position, data_type, "
                    " type_class, is_nullable) "
                    "VALUES (:t, 'id', 1, 'bigint', 'INT_WIDE', false) "
                    "RETURNING column_id"
                ),
                {"t": legacy_id},
            ).scalar()
            # No fk_candidates yet → both are orphans.

        cfg = SimpleNamespace(
            storage=SimpleNamespace(
                base_path=str(parquet_dir),
                parquet_cap_bytes=1_000_000_000,
            )
        )

        deleted = cleanup.gc_orphaned_parquet(pg_engine, cfg)
        assert keeper.exists() is False
        assert orphan.exists() is False
        assert len(deleted) == 2

        # Restore files and add a candidate that uses both columns.
        _touch(keeper, b"o" * 200)
        _touch(orphan, b"l" * 100)
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO discovery.fk_candidates "
                    "(child_col_id, parent_col_id, type_match, source_stage) "
                    "VALUES (:c, :p, true, 'sql_prefilter')"
                ),
                {"c": orders_col, "p": customers_col},
            )

        # Now the survivor set includes BOTH tables → no deletions.
        deleted2 = cleanup.gc_orphaned_parquet(pg_engine, cfg)
        assert keeper.exists() is True
        assert orphan.exists() is True
        assert deleted2 == []
