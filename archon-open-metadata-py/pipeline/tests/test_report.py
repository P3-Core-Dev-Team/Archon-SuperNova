"""
test_report.py — Integration tests for report.py against a real Postgres DB.

Strategy
--------
We spin up an ephemeral Postgres container via testcontainers-python, apply
the minimum schema DDL needed by the report queries (tbl_inventory,
col_inventory, relationships, pii_findings, run_log), insert one row for each,
and assert that:

1. ``report_relationships`` writes a CSV with exactly one data row containing
   the expected child/parent schema + table + column names.
2. ``report_pii`` writes a CSV with exactly one data row containing the
   expected schema, table, column, and pii_type.
3. ``report_exclusions`` writes a CSV with exactly one excluded table row.
4. ``report_summary`` writes a ``summary.md`` with relationship/PII counts.
5. ``generate_all`` produces all four report file pairs.

The tests run only if ``testcontainers`` is importable and Docker is available.
Mark ``@pytest.mark.integration`` so CI can opt in/out separately.

DDL note
--------
We inline a minimal version of the schema (just the tables/columns needed for
the report queries) rather than depending on pipeline/sql/results_schema.sql
landing in a specific path.  This keeps the test self-contained.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip if testcontainers / docker not available
# ---------------------------------------------------------------------------

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore

    HAS_TESTCONTAINERS = True
except ImportError:
    HAS_TESTCONTAINERS = False

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Minimal schema DDL
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

CREATE TABLE IF NOT EXISTS relationships (
    rel_id            BIGSERIAL PRIMARY KEY,
    child_col_id      BIGINT NOT NULL REFERENCES col_inventory,
    parent_col_id     BIGINT NOT NULL REFERENCES col_inventory,
    containment_full  REAL,
    cardinality       TEXT NOT NULL,
    confidence        REAL,
    evidence          JSONB,
    validated_locally BOOLEAN NOT NULL DEFAULT true,
    validation_method TEXT NOT NULL DEFAULT 'local_duckdb_full',
    discovered_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (child_col_id, parent_col_id)
);

CREATE TABLE IF NOT EXISTS pii_findings (
    finding_id        BIGSERIAL PRIMARY KEY,
    column_id         BIGINT NOT NULL REFERENCES col_inventory,
    pii_type          TEXT NOT NULL,
    detector          TEXT NOT NULL,
    match_count       INT NOT NULL,
    sample_count      INT NOT NULL,
    match_rate        REAL NOT NULL,
    validated         BOOLEAN NOT NULL DEFAULT false,
    redacted_examples JSONB,
    detected_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_log (
    log_id            BIGSERIAL PRIMARY KEY,
    phase             TEXT NOT NULL,
    scope_type        TEXT NOT NULL,
    scope_id          BIGINT,
    status            TEXT NOT NULL,
    started_at        TIMESTAMPTZ DEFAULT now(),
    ended_at          TIMESTAMPTZ,
    error_message     TEXT,
    metadata          JSONB,
    UNIQUE (phase, scope_type, scope_id)
);
"""

# ---------------------------------------------------------------------------
# Fixture: ephemeral Postgres with seed data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_engine():
    """
    Spin up a Postgres container, apply schema, insert fixture rows.
    Yield a SQLAlchemy engine; teardown handled by testcontainers.
    """
    if not HAS_TESTCONTAINERS:
        pytest.skip("testcontainers-python not installed")

    import sqlalchemy  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url()
        engine = sqlalchemy.create_engine(url, future=True)

        with engine.begin() as conn:
            # Apply schema DDL (split on statement boundaries)
            for stmt in _SCHEMA_DDL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))

        # ------------------------------------------------------------------
        # Seed: two tables, two columns each; one relationship; one PII finding
        # ------------------------------------------------------------------
        with engine.begin() as conn:
            # Table 1: orders
            conn.execute(
                text(
                    "INSERT INTO discovery.tbl_inventory "
                    "(schema_name, table_name, status) "
                    "VALUES ('public', 'orders', 'analyzed')"
                )
            )
            orders_id = conn.execute(
                text(
                    "SELECT table_id FROM discovery.tbl_inventory "
                    "WHERE table_name='orders'"
                )
            ).scalar()

            # Table 2: customers  (excluded)
            conn.execute(
                text(
                    "INSERT INTO discovery.tbl_inventory "
                    "(schema_name, table_name, status, exclusion_reason, "
                    " row_count_estimate, byte_size_estimate) "
                    "VALUES ('public', 'customers', 'excluded', 'log_pattern', 50000, 1048576)"
                )
            )
            customers_id = conn.execute(
                text(
                    "SELECT table_id FROM discovery.tbl_inventory "
                    "WHERE table_name='customers'"
                )
            ).scalar()

            # Columns for orders
            conn.execute(
                text(
                    "INSERT INTO discovery.col_inventory "
                    "(table_id, column_name, ordinal_position, data_type, "
                    " type_class, is_nullable) "
                    "VALUES (:t, 'customer_id', 1, 'bigint', 'INT_WIDE', false)"
                ),
                {"t": orders_id},
            )
            child_col_id = conn.execute(
                text(
                    "SELECT column_id FROM discovery.col_inventory "
                    "WHERE table_id=:t AND column_name='customer_id'"
                ),
                {"t": orders_id},
            ).scalar()

            # Columns for customers
            conn.execute(
                text(
                    "INSERT INTO discovery.col_inventory "
                    "(table_id, column_name, ordinal_position, data_type, "
                    " type_class, is_nullable) "
                    "VALUES (:t, 'id', 1, 'bigint', 'INT_WIDE', false)"
                ),
                {"t": customers_id},
            )
            parent_col_id = conn.execute(
                text(
                    "SELECT column_id FROM discovery.col_inventory "
                    "WHERE table_id=:t AND column_name='id'"
                ),
                {"t": customers_id},
            ).scalar()

            conn.execute(
                text(
                    "INSERT INTO discovery.col_inventory "
                    "(table_id, column_name, ordinal_position, data_type, "
                    " type_class, is_nullable) "
                    "VALUES (:t, 'email', 2, 'text', 'STRING_SHORT', true)"
                ),
                {"t": customers_id},
            )
            email_col_id = conn.execute(
                text(
                    "SELECT column_id FROM discovery.col_inventory "
                    "WHERE table_id=:t AND column_name='email'"
                ),
                {"t": customers_id},
            ).scalar()

            # One relationship: orders.customer_id → customers.id
            conn.execute(
                text(
                    "INSERT INTO discovery.relationships "
                    "(child_col_id, parent_col_id, containment_full, "
                    " cardinality, confidence) "
                    "VALUES (:c, :p, 0.98, 'MANY_TO_ONE', 0.97)"
                ),
                {"c": child_col_id, "p": parent_col_id},
            )

            # One PII finding: customers.email → EMAIL
            conn.execute(
                text(
                    "INSERT INTO discovery.pii_findings "
                    "(column_id, pii_type, detector, match_count, "
                    " sample_count, match_rate, validated, "
                    " redacted_examples) "
                    "VALUES (:col, 'EMAIL', 'hyperscan', 9, 10, 0.9, true, "
                    " '[\"al***@example.com\"]'::jsonb)"
                ),
                {"col": email_col_id},
            )

            # One run_log entry for completed inventory phase
            conn.execute(
                text(
                    "INSERT INTO discovery.run_log "
                    "(phase, scope_type, scope_id, status) "
                    "VALUES ('inventory', 'global', NULL, 'succeeded')"
                )
            )

        yield engine


# ---------------------------------------------------------------------------
# Helper: read CSV into list-of-dicts
# ---------------------------------------------------------------------------


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_TESTCONTAINERS, reason="testcontainers not available")
class TestReportRelationships:
    def test_csv_has_one_row(self, pg_engine, tmp_path):
        from discovery.report import report_relationships

        paths = report_relationships(pg_engine, tmp_path)
        csv_path = next(p for p in paths if p.suffix == ".csv")
        rows = _read_csv(csv_path)
        assert len(rows) == 1

    def test_csv_column_values(self, pg_engine, tmp_path):
        from discovery.report import report_relationships

        paths = report_relationships(pg_engine, tmp_path)
        csv_path = next(p for p in paths if p.suffix == ".csv")
        row = _read_csv(csv_path)[0]

        assert row["child_schema"] == "public"
        assert row["child_table"] == "orders"
        assert row["child_col"] == "customer_id"
        assert row["parent_schema"] == "public"
        assert row["parent_table"] == "customers"
        assert row["parent_col"] == "id"
        assert row["cardinality"] == "MANY_TO_ONE"
        assert float(row["containment_full"]) == pytest.approx(0.98, abs=1e-3)

    def test_xlsx_written(self, pg_engine, tmp_path):
        from discovery.report import report_relationships

        paths = report_relationships(pg_engine, tmp_path)
        xlsx_path = next(p for p in paths if p.suffix == ".xlsx")
        assert xlsx_path.exists()
        assert xlsx_path.stat().st_size > 0


@pytest.mark.skipif(not HAS_TESTCONTAINERS, reason="testcontainers not available")
class TestReportPii:
    def test_csv_has_one_row(self, pg_engine, tmp_path):
        from discovery.report import report_pii

        paths = report_pii(pg_engine, tmp_path)
        csv_path = next(p for p in paths if p.suffix == ".csv")
        rows = _read_csv(csv_path)
        assert len(rows) == 1

    def test_csv_column_values(self, pg_engine, tmp_path):
        from discovery.report import report_pii

        paths = report_pii(pg_engine, tmp_path)
        csv_path = next(p for p in paths if p.suffix == ".csv")
        row = _read_csv(csv_path)[0]

        assert row["schema"] == "public"
        assert row["table"] == "customers"
        assert row["column"] == "email"
        assert row["pii_type"] == "EMAIL"
        assert row["detector"] == "hyperscan"
        assert int(row["match_count"]) == 9
        assert float(row["match_rate"]) == pytest.approx(0.9, abs=1e-3)
        assert row["validated"].lower() in ("true", "t", "1")

    def test_redacted_examples_is_json_string(self, pg_engine, tmp_path):
        from discovery.report import report_pii

        paths = report_pii(pg_engine, tmp_path)
        csv_path = next(p for p in paths if p.suffix == ".csv")
        row = _read_csv(csv_path)[0]

        # redacted_examples should be parseable as JSON
        examples = json.loads(row["redacted_examples"])
        assert isinstance(examples, list)
        assert len(examples) >= 1

    def test_xlsx_written(self, pg_engine, tmp_path):
        from discovery.report import report_pii

        paths = report_pii(pg_engine, tmp_path)
        xlsx_path = next(p for p in paths if p.suffix == ".xlsx")
        assert xlsx_path.exists()
        assert xlsx_path.stat().st_size > 0


@pytest.mark.skipif(not HAS_TESTCONTAINERS, reason="testcontainers not available")
class TestReportExclusions:
    def test_csv_has_one_row(self, pg_engine, tmp_path):
        from discovery.report import report_exclusions

        paths = report_exclusions(pg_engine, tmp_path)
        csv_path = next(p for p in paths if p.suffix == ".csv")
        rows = _read_csv(csv_path)
        assert len(rows) == 1

    def test_csv_column_values(self, pg_engine, tmp_path):
        from discovery.report import report_exclusions

        paths = report_exclusions(pg_engine, tmp_path)
        csv_path = next(p for p in paths if p.suffix == ".csv")
        row = _read_csv(csv_path)[0]

        assert row["schema"] == "public"
        assert row["table"] == "customers"
        assert row["exclusion_reason"] == "log_pattern"
        assert int(row["row_count_estimate"]) == 50000


@pytest.mark.skipif(not HAS_TESTCONTAINERS, reason="testcontainers not available")
class TestReportSummary:
    def test_summary_md_written(self, pg_engine, tmp_path):
        from discovery.report import report_summary

        paths = report_summary(pg_engine, tmp_path)
        assert len(paths) == 1
        md_path = paths[0]
        assert md_path.name == "summary.md"
        assert md_path.exists()

    def test_summary_contains_counts(self, pg_engine, tmp_path):
        from discovery.report import report_summary

        paths = report_summary(pg_engine, tmp_path)
        md_content = paths[0].read_text(encoding="utf-8")

        # Should mention relationship count (1) and PII finding count (1)
        assert "1" in md_content
        # Should have the markdown table header
        assert "Phase" in md_content or "phase" in md_content.lower()


@pytest.mark.skipif(not HAS_TESTCONTAINERS, reason="testcontainers not available")
class TestGenerateAll:
    def test_all_files_produced(self, pg_engine, tmp_path):
        from discovery.report import generate_all

        class FakeConfig:
            class reporting:
                output_dir = str(tmp_path)

        paths = generate_all(pg_engine, FakeConfig())

        names = {p.name for p in paths}
        assert "relationships.csv" in names
        assert "relationships.xlsx" in names
        assert "pii_findings.csv" in names
        assert "pii_findings.xlsx" in names
        assert "exclusions.csv" in names
        assert "exclusions.xlsx" in names
        assert "summary.md" in names

    def test_total_file_count(self, pg_engine, tmp_path):
        from discovery.report import generate_all

        class FakeConfig:
            class reporting:
                output_dir = str(tmp_path)

        paths = generate_all(pg_engine, FakeConfig())
        # 2 (relationships) + 2 (pii) + 2 (exclusions) + 1 (summary) = 7
        assert len(paths) == 7
