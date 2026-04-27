"""
test_inventory_joiner.py — Unit tests for _compute_index_flags.

The joiner takes four pg_catalog parquet extracts (pg_index, pg_attribute,
pg_class, pg_namespace) and derives per-(table, column) is_indexed and
is_unique_indexed booleans.  Exercised in isolation here using small in-memory
parquet fixtures so no Docker / source DB is required.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from discovery.inventory import _compute_index_flags


def _write_parquet(path: Path, rows: list[dict]) -> str:
    """Materialise *rows* as a parquet file at *path*; return the path string."""
    if not rows:
        # Empty parquet — the joiner only checks Path(p).exists(), but DuckDB
        # needs a file with a schema, so write an empty pa.Table instead.
        table = pa.table({})
    else:
        table = pa.Table.from_pylist(rows)
    pq.write_table(table, str(path))
    return str(path)


def _make_fixture_set(tmp_path: Path) -> dict[str, str]:
    """
    Build a tiny pg_catalog snapshot for a fictional schema 'public' with two
    tables (orders, customers) and three columns (orders.id PK, orders.cust_id
    indexed but not unique, customers.email UNIQUE-indexed).

    OIDs:
       namespace 'public' -> 2200
       relations: orders=10001, customers=10002

    Indexes (relation oid + indkey + flags):
       orders     pk on attnum=1   indisunique=true,  indisprimary=true
       orders     idx on attnum=2  indisunique=false, indisprimary=false
       customers  uq on attnum=1   indisunique=true,  indisprimary=false

    pg_attribute:
       orders     attnum=1 → id
       orders     attnum=2 → cust_id
       customers  attnum=1 → email
    """
    pg_index = _write_parquet(
        tmp_path / "pg_index.parquet",
        [
            {"indrelid": 10001, "indkey": "1",  "indisunique": True,  "indisprimary": True},
            {"indrelid": 10001, "indkey": "2",  "indisunique": False, "indisprimary": False},
            {"indrelid": 10002, "indkey": "1",  "indisunique": True,  "indisprimary": False},
        ],
    )
    pg_attr = _write_parquet(
        tmp_path / "pg_attribute.parquet",
        [
            {"attrelid": 10001, "attnum": 1, "attname": "id"},
            {"attrelid": 10001, "attnum": 2, "attname": "cust_id"},
            {"attrelid": 10002, "attnum": 1, "attname": "email"},
            # Noise: a column in a different relation must not match.
            {"attrelid": 99999, "attnum": 1, "attname": "noise"},
        ],
    )
    pg_class = _write_parquet(
        tmp_path / "pg_class.parquet",
        [
            {"oid": 10001, "relname": "orders",    "relnamespace": 2200},
            {"oid": 10002, "relname": "customers", "relnamespace": 2200},
            # Noise relation in a different namespace.
            {"oid": 99999, "relname": "system",    "relnamespace": 9999},
        ],
    )
    pg_ns = _write_parquet(
        tmp_path / "pg_namespace.parquet",
        [
            {"oid": 2200, "nspname": "public"},
            {"oid": 9999, "nspname": "system"},
        ],
    )
    return {
        "pg_index_path": pg_index,
        "pg_attr_path": pg_attr,
        "pg_class_path": pg_class,
        "pg_ns_path": pg_ns,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestComputeIndexFlags:
    def test_returns_correct_indexed_columns(self, tmp_path: Path) -> None:
        paths = _make_fixture_set(tmp_path)
        con = duckdb.connect(":memory:")
        try:
            indexed, unique_indexed = _compute_index_flags(
                con, "public", **paths
            )
        finally:
            con.close()

        assert indexed == {
            ("orders", "id"),
            ("orders", "cust_id"),
            ("customers", "email"),
        }

    def test_returns_correct_unique_indexed_columns(self, tmp_path: Path) -> None:
        paths = _make_fixture_set(tmp_path)
        con = duckdb.connect(":memory:")
        try:
            indexed, unique_indexed = _compute_index_flags(
                con, "public", **paths
            )
        finally:
            con.close()

        # cust_id is NOT unique; id (PK) and email (UNIQUE) ARE.
        assert unique_indexed == {
            ("orders", "id"),
            ("customers", "email"),
        }
        assert ("orders", "cust_id") not in unique_indexed

    def test_namespace_filter_excludes_other_schemas(self, tmp_path: Path) -> None:
        """A relation in a different namespace must not appear in the result."""
        paths = _make_fixture_set(tmp_path)
        con = duckdb.connect(":memory:")
        try:
            indexed, _ = _compute_index_flags(con, "public", **paths)
        finally:
            con.close()

        # 'noise' is in attrelid=99999 which is in namespace 9999 ('system').
        assert all(t != "system" for (t, _c) in indexed)

    def test_missing_path_returns_empty_sets(self, tmp_path: Path) -> None:
        """If any of the four parquet paths is None, both sets are empty."""
        paths = _make_fixture_set(tmp_path)
        con = duckdb.connect(":memory:")
        try:
            indexed, unique_indexed = _compute_index_flags(
                con,
                "public",
                pg_index_path=None,
                pg_attr_path=paths["pg_attr_path"],
                pg_class_path=paths["pg_class_path"],
                pg_ns_path=paths["pg_ns_path"],
            )
        finally:
            con.close()

        assert indexed == set()
        assert unique_indexed == set()

    def test_nonexistent_path_returns_empty_sets(self, tmp_path: Path) -> None:
        """Paths that point to missing files return empty sets, not exceptions."""
        paths = _make_fixture_set(tmp_path)
        con = duckdb.connect(":memory:")
        try:
            indexed, unique_indexed = _compute_index_flags(
                con,
                "public",
                pg_index_path=str(tmp_path / "does_not_exist.parquet"),
                pg_attr_path=paths["pg_attr_path"],
                pg_class_path=paths["pg_class_path"],
                pg_ns_path=paths["pg_ns_path"],
            )
        finally:
            con.close()

        assert indexed == set()
        assert unique_indexed == set()

    def test_invalid_schema_name_raises(self, tmp_path: Path) -> None:
        """Schema name must match the Postgres identifier grammar."""
        paths = _make_fixture_set(tmp_path)
        con = duckdb.connect(":memory:")
        try:
            with pytest.raises(ValueError, match="Invalid schema name"):
                _compute_index_flags(con, "1bad-name; DROP TABLE x;", **paths)
        finally:
            con.close()

    def test_unknown_schema_returns_empty_sets(self, tmp_path: Path) -> None:
        """A valid identifier with no matching namespace yields empty sets."""
        paths = _make_fixture_set(tmp_path)
        con = duckdb.connect(":memory:")
        try:
            indexed, unique_indexed = _compute_index_flags(
                con, "no_such_schema", **paths
            )
        finally:
            con.close()

        assert indexed == set()
        assert unique_indexed == set()

    def test_multi_column_indkey_expands(self, tmp_path: Path) -> None:
        """A composite-index indkey 'attnum1 attnum2' marks BOTH columns indexed."""
        pg_index = _write_parquet(
            tmp_path / "pg_index.parquet",
            [
                {"indrelid": 10001, "indkey": "1 2", "indisunique": True, "indisprimary": False},
            ],
        )
        pg_attr = _write_parquet(
            tmp_path / "pg_attribute.parquet",
            [
                {"attrelid": 10001, "attnum": 1, "attname": "first_name"},
                {"attrelid": 10001, "attnum": 2, "attname": "last_name"},
            ],
        )
        pg_class = _write_parquet(
            tmp_path / "pg_class.parquet",
            [{"oid": 10001, "relname": "people", "relnamespace": 2200}],
        )
        pg_ns = _write_parquet(
            tmp_path / "pg_namespace.parquet",
            [{"oid": 2200, "nspname": "public"}],
        )
        con = duckdb.connect(":memory:")
        try:
            indexed, unique_indexed = _compute_index_flags(
                con,
                "public",
                pg_index_path=pg_index,
                pg_attr_path=pg_attr,
                pg_class_path=pg_class,
                pg_ns_path=pg_ns,
            )
        finally:
            con.close()

        assert indexed == {("people", "first_name"), ("people", "last_name")}
        assert unique_indexed == {("people", "first_name"), ("people", "last_name")}
