"""Tests that catch the schema-vs-data mismatches called out in the synthetic-data review.

- wide_denormalized must have exactly the column count declared in the manifest.
- order_items.unit_price must match the canonical product.price for each product_id.
- payments.amount must match the SUM(quantity * unit_price * (1-discount)) for the order.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pyarrow.parquet as pq
import pytest

SYNTHETIC_DIR = Path(os.environ.get("SYNTHETIC_DIR", "./synthetic"))
SCHEMAS_DIR = SYNTHETIC_DIR / "schemas"


def _require_parquet(name: str) -> Path:
    p = SCHEMAS_DIR / f"{name}.parquet"
    if not p.exists():
        pytest.skip(f"Parquet file not found: {p}. Run generation first.")
    return p


def _load_ground_truth() -> dict:
    path = SYNTHETIC_DIR / "ground_truth.json"
    if not path.exists():
        pytest.skip(f"ground_truth.json not found at {path}. Run generation first.")
    return json.loads(path.read_text())


class TestWideDenormalizedColumnCount:
    """C1 from the review: parquet columns must equal manifest columns."""

    def test_parquet_column_count_matches_manifest(self):
        path = _require_parquet("wide_denormalized")
        schema = pq.read_schema(path)
        gt = _load_ground_truth()
        wide_table = next(
            (t for t in gt["tables"] if t["name"] == "wide_denormalized"),
            None,
        )
        assert wide_table is not None, "wide_denormalized missing from manifest"
        manifest_cols = len(wide_table["columns"])
        actual_cols = len(schema)
        assert actual_cols == manifest_cols, (
            f"wide_denormalized has {actual_cols} columns in parquet but "
            f"{manifest_cols} declared in manifest"
        )

    def test_parquet_has_exactly_250_columns(self):
        """Spec calls for id + col_001..col_249 = 250 columns total."""
        path = _require_parquet("wide_denormalized")
        schema = pq.read_schema(path)
        assert len(schema) == 250, (
            f"wide_denormalized must have exactly 250 columns, got {len(schema)}"
        )

    def test_no_created_at_in_wide(self):
        """The earlier bug was an extra `created_at` column in the parquet."""
        path = _require_parquet("wide_denormalized")
        schema = pq.read_schema(path)
        assert "created_at" not in schema.names, (
            "wide_denormalized must not have a created_at column "
            "(was 251-column ghost in earlier versions)"
        )


class TestProductPricesMatch:
    """C2 from the review: order_items.unit_price MUST equal products.price[product_id]."""

    def test_unit_price_matches_products(self):
        prods_path = _require_parquet("products")
        items_path = _require_parquet("order_items")
        prods = pq.read_table(prods_path, columns=["id", "price"]).to_pandas()
        items = pq.read_table(
            items_path, columns=["product_id", "unit_price"]
        ).to_pandas().head(100)
        assert len(items) >= 100, "Need at least 100 order_items rows to sample"
        price_map = dict(zip(prods["id"].astype(int), prods["price"].astype(float)))

        mismatches = []
        for _, row in items.iterrows():
            pid = int(row["product_id"])
            expected = price_map.get(pid)
            actual = float(row["unit_price"])
            if expected is None or abs(expected - actual) > 1e-6:
                mismatches.append((pid, expected, actual))
        assert not mismatches, (
            f"order_items.unit_price doesn't match products.price for "
            f"{len(mismatches)}/100 sampled rows. First few: {mismatches[:5]}"
        )

    def test_payments_amount_matches_order_subtotals(self):
        """payments.amount should equal SUM(qty*unit_price*(1-disc/100)) per order_id."""
        items_path = _require_parquet("order_items")
        pay_path = _require_parquet("payments")
        items = pq.read_table(
            items_path,
            columns=["order_id", "quantity", "unit_price", "discount_pct"],
        ).to_pandas()
        items["line_total"] = (
            items["unit_price"] * items["quantity"] * (1 - items["discount_pct"] / 100.0)
        )
        subtotals = items.groupby("order_id")["line_total"].sum().to_dict()

        payments = pq.read_table(pay_path, columns=["order_id", "amount"]).to_pandas()
        sample = payments.head(100)
        # Match-rate >= 90% (some payments may be for orders with no items
        # in small mode; those use a fallback random amount).
        matches = 0
        seen_with_subtotal = 0
        tolerance = 1e-3
        for _, row in sample.iterrows():
            oid = int(row["order_id"])
            if oid not in subtotals:
                continue
            seen_with_subtotal += 1
            expected = subtotals[oid]
            # payments.amount is clamped to [0.01, 1_000_000.0]; allow that.
            clamped = max(0.01, min(1_000_000.0, expected))
            if abs(clamped - float(row["amount"])) <= max(
                tolerance, abs(clamped) * 1e-6
            ):
                matches += 1
        assert seen_with_subtotal >= 50, (
            f"Too few payments matched to orders with items "
            f"({seen_with_subtotal}/100); cannot make a meaningful assertion"
        )
        ratio = matches / seen_with_subtotal
        assert ratio >= 0.95, (
            f"Only {ratio:.2%} of payments.amount match the per-order subtotal; "
            f"expected ≥95% (was a known bug C2 — recomputed product prices)"
        )
