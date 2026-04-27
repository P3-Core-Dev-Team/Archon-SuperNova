"""Test that all declared FK relationships have 100% containment."""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

SYNTHETIC_DIR = Path(os.environ.get("SYNTHETIC_DIR", "./synthetic"))
SCHEMAS_DIR = SYNTHETIC_DIR / "schemas"


def _load_ids(table_name: str, column_name: str) -> set:
    """Load all non-null values from a column."""
    path = SCHEMAS_DIR / f"{table_name}.parquet"
    if not path.exists():
        pytest.skip(f"Missing: {path}. Run generation first.")
    tbl = pq.read_table(path, columns=[column_name])
    col = tbl.column(column_name)
    return {v.as_py() for v in col if v.is_valid and v.as_py() is not None}


def _check_containment(
    child_table: str,
    child_col: str,
    parent_table: str,
    parent_col: str,
    allow_null: bool = True,
) -> tuple[int, int, float]:
    """
    Returns (n_valid_child, n_missing, containment_rate).
    """
    parent_ids = _load_ids(parent_table, parent_col)

    child_path = SCHEMAS_DIR / f"{child_table}.parquet"
    if not child_path.exists():
        pytest.skip(f"Missing: {child_path}")

    tbl = pq.read_table(child_path, columns=[child_col])
    col = tbl.column(child_col)

    total = 0
    missing = 0
    for v in col:
        if not v.is_valid or v.as_py() is None:
            continue  # null is OK if declared nullable
        total += 1
        val = v.as_py()
        if val not in parent_ids:
            missing += 1

    containment = 1.0 - (missing / total) if total > 0 else 1.0
    return total, missing, containment


# ---------------------------------------------------------------------------
# FK test cases derived from config
# ---------------------------------------------------------------------------

FK_CASES = [
    # (child_table, child_col, parent_table, parent_col)
    ("addresses", "customer_id", "customers", "id"),
    ("products", "category_id", "categories", "id"),
    ("orders", "customer_id", "customers", "id"),
    ("orders", "shipping_address_id", "addresses", "id"),
    ("order_items", "order_id", "orders", "id"),
    ("order_items", "product_id", "products", "id"),
    ("payments", "order_id", "orders", "id"),
    ("payments", "customer_id", "customers", "id"),
    ("inventory", "product_id", "products", "id"),
    ("warehouse_stock", "product_id", "products", "id"),
    ("warehouse_stock", "warehouse_id", "warehouses", "id"),
    ("users", "customer_id", "customers", "id"),
    ("user_roles", "user_id", "users", "id"),
    ("user_roles", "role_id", "roles", "id"),
    ("user_sessions", "user_id", "users", "id"),
    ("api_tokens", "user_id", "users", "id"),
    ("departments", "head_employee_id", "employee_records", "id"),
    ("tickets", "customer_id", "customers", "id"),
    ("tickets", "assigned_to", "employee_records", "id"),
    ("ticket_messages", "ticket_id", "tickets", "id"),
    ("ticket_messages", "author_user_id", "users", "id"),
    ("reviews", "product_id", "products", "id"),
    ("reviews", "customer_id", "customers", "id"),
    # Self-referencing FKs
    ("categories", "parent_category_id", "categories", "id"),
    ("employee_records", "manager_id", "employee_records", "id"),
]


@pytest.mark.parametrize("child_table,child_col,parent_table,parent_col", FK_CASES)
def test_fk_containment(child_table, child_col, parent_table, parent_col):
    """Assert 100% containment for every declared FK (excluding null values)."""
    total, missing, rate = _check_containment(
        child_table, child_col, parent_table, parent_col
    )
    assert missing == 0, (
        f"FK {child_table}.{child_col} → {parent_table}.{parent_col}: "
        f"{missing}/{total} non-null values not found in parent. "
        f"Containment: {rate:.4f}"
    )
