"""Test that ground_truth.json parses correctly via the GroundTruth Pydantic model."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

SYNTHETIC_DIR = Path(os.environ.get("SYNTHETIC_DIR", "./synthetic"))


def _load_ground_truth():
    path = SYNTHETIC_DIR / "ground_truth.json"
    if not path.exists():
        pytest.skip(f"ground_truth.json not found at {path}. Run generation first.")
    return json.loads(path.read_text())


class TestManifestSchema:
    def test_parses_via_pydantic(self):
        """ground_truth.json must parse without validation errors."""
        from synthetic_data.manifest import GroundTruth
        data = _load_ground_truth()
        gt = GroundTruth(**data)
        assert gt.generator_version == "1.0.0"
        assert isinstance(gt.seed, int)
        assert isinstance(gt.tables, list)

    def test_table_count(self):
        """Must have at least 30 tables (spec lists 31 distinct tables)."""
        data = _load_ground_truth()
        assert len(data["tables"]) >= 30, (
            f"Expected at least 30 tables, got {len(data['tables'])}"
        )

    def test_all_table_names_present(self):
        """All 30 expected table names must be present."""
        from synthetic_data.config import ALL_TABLES
        expected = {t.name for t in ALL_TABLES}
        data = _load_ground_truth()
        actual = {t["name"] for t in data["tables"]}
        missing = expected - actual
        assert len(missing) == 0, f"Missing tables in manifest: {missing}"

    def test_exclusions_present(self):
        """Must have exclusion entries for all noise tables."""
        data = _load_ground_truth()
        excluded_tables = {
            e["table"] for e in data["expected_exclusions"]
        }
        expected_excluded = {
            "audit_log", "access_log", "temp_import_batch",
            "tmp_staging_orders", "orders_bak_20240101",
            "customers_archive", "user_events", "etl_import_queue",
            "migrations",
        }
        missing = expected_excluded - excluded_tables
        assert len(missing) == 0, f"Missing exclusion entries: {missing}"

    def test_foreign_keys_present(self):
        """Must have FK entries for all declared relationships."""
        data = _load_ground_truth()
        fk_pairs = {
            (fk["child_table"], fk["child_column"])
            for fk in data["expected_foreign_keys"]
        }
        expected_fks = {
            ("addresses", "customer_id"),
            ("products", "category_id"),
            ("orders", "customer_id"),
            ("orders", "shipping_address_id"),
            ("order_items", "order_id"),
            ("order_items", "product_id"),
            ("payments", "order_id"),
            ("payments", "customer_id"),
            ("inventory", "product_id"),
            ("warehouse_stock", "product_id"),
            ("warehouse_stock", "warehouse_id"),
            ("users", "customer_id"),
            ("user_roles", "user_id"),
            ("user_roles", "role_id"),
            ("user_sessions", "user_id"),
            ("api_tokens", "user_id"),
            ("departments", "head_employee_id"),
            ("tickets", "customer_id"),
            ("tickets", "assigned_to"),
            ("ticket_messages", "ticket_id"),
            ("ticket_messages", "author_user_id"),
            ("reviews", "product_id"),
            ("reviews", "customer_id"),
            ("categories", "parent_category_id"),
            ("employee_records", "manager_id"),
        }
        missing = expected_fks - fk_pairs
        assert len(missing) == 0, f"Missing FK entries: {missing}"

    def test_pii_columns_annotated(self):
        """PII columns must have at least one pii type listed."""
        data = _load_ground_truth()
        tables_by_name = {t["name"]: t for t in data["tables"]}

        pii_checks = [
            ("customers", "email", "EMAIL"),
            ("customers", "phone", "PHONE"),
            ("employee_records", "ssn", "SSN"),
            ("payments", "card_number_raw", "CREDIT_CARD"),
            ("payments", "iban", "IBAN"),
        ]
        for table_name, col_name, pii_type in pii_checks:
            table = tables_by_name.get(table_name)
            assert table is not None, f"Table {table_name} not in manifest"
            col = next(
                (c for c in table["columns"] if c["name"] == col_name), None
            )
            assert col is not None, f"Column {table_name}.{col_name} not in manifest"
            assert pii_type in col["pii"], (
                f"{table_name}.{col_name} should have PII type {pii_type}, "
                f"got: {col['pii']}"
            )

    def test_row_counts_positive(self):
        """All non-empty tables should have positive row counts."""
        data = _load_ground_truth()
        for table in data["tables"]:
            if table["name"] != "empty_table":
                assert table["rows"] > 0, f"Table {table['name']} has 0 rows"

    def test_empty_table_zero_rows(self):
        """empty_table must have 0 rows."""
        data = _load_ground_truth()
        tables_by_name = {t["name"]: t for t in data["tables"]}
        assert "empty_table" in tables_by_name
        assert tables_by_name["empty_table"]["rows"] == 0

    def test_metadata_json_valid(self):
        """metadata.json must be valid JSON with expected keys."""
        meta_path = SYNTHETIC_DIR / "metadata.json"
        if not meta_path.exists():
            pytest.skip(f"metadata.json not found at {meta_path}")
        meta = json.loads(meta_path.read_text())
        assert "generator_version" in meta
        assert "seed" in meta
        assert "generated_at" in meta
        assert "tables" in meta
        assert isinstance(meta["tables"], dict)
