"""
test_inventory.py — Integration test for Phase 1 inventory.

Uses an ephemeral source Postgres (testcontainers via conftest.py) seeded with
business + noise tables.  The extraction_client is MOCKED: it reads from the
seeded source DB directly (psycopg2) and writes temporary Parquet files that
the inventory code then reads via DuckDB.

These tests exercise the public ``inventory.run_phase_1`` entry point.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import psycopg2
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

import discovery.inventory as inventory_module
from discovery.extraction_client import ExtractionClient
from discovery.models import (
    ConnectionConfig,
    ExtractionManifest,
    ExtractionResponse,
    ExtractionStatus,
    ManifestEntry,
)
from discovery.results_db import col_inventory_t, tbl_inventory_t


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed SQL (overrides fixtures/seed_source.sql for this test's purposes)
# ---------------------------------------------------------------------------

SEED_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id BIGINT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(64),
    created_at TIMESTAMP WITHOUT TIME ZONE,
    is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS orders (
    order_id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL,
    total_amount NUMERIC(10,2),
    status VARCHAR(32),
    ordered_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE TABLE IF NOT EXISTS order_items (
    item_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    product_id INTEGER,
    quantity SMALLINT,
    unit_price NUMERIC(10,2)
);

-- Noise tables (should be excluded)
CREATE TABLE IF NOT EXISTS access_log (
    log_id BIGSERIAL PRIMARY KEY,
    event_type TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS temp_import (
    id BIGINT,
    payload TEXT
);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def source_dsn(source_db_url: str) -> str:
    return source_db_url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


@pytest.fixture
def seeded_source(source_dsn: str) -> str:
    conn = psycopg2.connect(source_dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SEED_SQL)
    conn.close()
    return source_dsn


@pytest.fixture
def fake_config(tmp_path: Path) -> SimpleNamespace:
    """Build a fake AppConfig that satisfies inventory.run_phase_1's needs."""
    src = SimpleNamespace(
        type="postgres",
        host="localhost",
        port=5432,
        database="source",
        user="postgres",
        password_secret_ref="env://SOURCE_DB_PASSWORD",
        ssl_mode="require",
        application_name="discovery-extractor",
        schemas=["public"],
        to_connection_config=lambda: ConnectionConfig(
            type="postgres",
            host="localhost",
            port=5432,
            database="source",
            user="postgres",
            password_secret_ref="env://SOURCE_DB_PASSWORD",
        ),
    )
    storage = SimpleNamespace(base_path=str(tmp_path))
    return SimpleNamespace(source_db=src, storage=storage)


def _build_mock_extraction_client(source_dsn: str) -> MagicMock:
    """Mock ExtractionClient that runs the SQL against the source DB directly
    and writes the result to the requested Parquet path."""
    import pandas as pd

    client = MagicMock(spec=ExtractionClient)

    def fake_extract_sync(req: Any) -> ExtractionResponse:
        output_path = req.output.path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        conn = psycopg2.connect(source_dsn)
        try:
            df = pd.read_sql(req.query, conn)
        finally:
            conn.close()

        # Coerce object columns to strings so PyArrow doesn't choke on mixed
        # postgres types (e.g. yes_or_no maps to bool but information_schema
        # uses 'YES'/'NO').
        table = pa.Table.from_pandas(df)
        pq.write_table(table, output_path)

        file_size = Path(output_path).stat().st_size
        return ExtractionResponse(
            extraction_id="test-extraction-id",
            status=ExtractionStatus.COMPLETED,
            manifest=ExtractionManifest(
                files=[
                    ManifestEntry(
                        path=output_path,
                        rows=len(df),
                        bytes=file_size,
                    )
                ]
            ),
        )

    client.extract_sync.side_effect = fake_extract_sync
    return client


def _clear_results(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM discovery.col_inventory"))
        conn.execute(text("DELETE FROM discovery.tbl_inventory"))
        conn.execute(text("DELETE FROM discovery.run_log"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunPhase1:
    def test_three_business_tables_inventoried(
        self,
        seeded_source: str,
        engine: Engine,
        fake_config: SimpleNamespace,
    ) -> None:
        _clear_results(engine)
        mock_client = _build_mock_extraction_client(seeded_source)

        inventory_module.run_phase_1(engine, mock_client, fake_config)

        with engine.connect() as conn:
            rows = conn.execute(
                select(tbl_inventory_t).where(tbl_inventory_t.c.status == "pending")
            ).mappings().all()

        table_names = {r["table_name"] for r in rows}
        assert table_names == {"customers", "orders", "order_items"}, (
            f"Unexpected tables in inventory: {table_names}"
        )

    def test_noise_tables_excluded(
        self,
        seeded_source: str,
        engine: Engine,
        fake_config: SimpleNamespace,
    ) -> None:
        _clear_results(engine)
        mock_client = _build_mock_extraction_client(seeded_source)

        inventory_module.run_phase_1(engine, mock_client, fake_config)

        with engine.connect() as conn:
            excluded_rows = conn.execute(
                select(tbl_inventory_t).where(tbl_inventory_t.c.status == "excluded")
            ).mappings().all()

        excluded_names = {r["table_name"] for r in excluded_rows}
        assert "access_log" in excluded_names
        assert "temp_import" in excluded_names

        for row in excluded_rows:
            assert row["exclusion_reason"] is not None

    def test_col_inventory_populated(
        self,
        seeded_source: str,
        engine: Engine,
        fake_config: SimpleNamespace,
    ) -> None:
        _clear_results(engine)
        mock_client = _build_mock_extraction_client(seeded_source)

        inventory_module.run_phase_1(engine, mock_client, fake_config)

        with engine.connect() as conn:
            col_rows = conn.execute(select(col_inventory_t)).mappings().all()

        assert len(col_rows) >= 10

        valid_classes = {
            "INT_NARROW", "INT_WIDE", "UUID", "STRING_SHORT", "STRING_LONG",
            "DATE", "TIMESTAMP", "BOOL", "FLOAT", "BINARY",
        }
        for row in col_rows:
            assert row["type_class"] in valid_classes

    def test_bool_column_not_fk_eligible(
        self,
        seeded_source: str,
        engine: Engine,
        fake_config: SimpleNamespace,
    ) -> None:
        _clear_results(engine)
        mock_client = _build_mock_extraction_client(seeded_source)

        inventory_module.run_phase_1(engine, mock_client, fake_config)

        with engine.connect() as conn:
            bool_cols = conn.execute(
                select(col_inventory_t).where(col_inventory_t.c.type_class == "BOOL")
            ).mappings().all()

        assert len(bool_cols) > 0
        for col in bool_cols:
            assert col["is_fk_eligible"] is False

    def test_idempotent_rerun(
        self,
        seeded_source: str,
        engine: Engine,
        fake_config: SimpleNamespace,
    ) -> None:
        _clear_results(engine)
        mock_client = _build_mock_extraction_client(seeded_source)

        inventory_module.run_phase_1(engine, mock_client, fake_config)

        # Reset the run_log so the second run actually executes again.
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM discovery.run_log WHERE phase='inventory'"))

        inventory_module.run_phase_1(engine, mock_client, fake_config)

        with engine.connect() as conn:
            tbl_count = conn.execute(
                text("SELECT COUNT(*) FROM discovery.tbl_inventory")
            ).scalar()
            distinct_tbls = conn.execute(
                text(
                    "SELECT COUNT(DISTINCT (schema_name, table_name)) "
                    "FROM discovery.tbl_inventory"
                )
            ).scalar()

        assert tbl_count == distinct_tbls

    def test_status_preserved_on_rerun(
        self,
        seeded_source: str,
        engine: Engine,
        fake_config: SimpleNamespace,
    ) -> None:
        """If a row has progressed past 'pending', a re-run must NOT revert it."""
        _clear_results(engine)
        mock_client = _build_mock_extraction_client(seeded_source)

        inventory_module.run_phase_1(engine, mock_client, fake_config)

        # Mark a non-excluded table as 'extracted' to simulate phase 2 progress.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE discovery.tbl_inventory SET status='extracted' "
                    "WHERE table_name='customers'"
                )
            )
            # Also clear the inventory run_log so phase 1 runs again.
            conn.execute(
                text("DELETE FROM discovery.run_log WHERE phase='inventory'")
            )

        inventory_module.run_phase_1(engine, mock_client, fake_config)

        with engine.connect() as conn:
            row = conn.execute(
                select(tbl_inventory_t).where(
                    tbl_inventory_t.c.table_name == "customers"
                )
            ).mappings().first()

        assert row["status"] == "extracted", (
            "Inventory re-run must not revert status from 'extracted' to 'pending'"
        )
