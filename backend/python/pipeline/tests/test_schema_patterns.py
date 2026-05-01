"""Tests for the schema-pattern helpers."""

from __future__ import annotations

import pytest

from discovery.schema_patterns import (
    KNOWN_SCHEMAS,
    bridge_tables,
    detect_temporal,
    match_known_schema,
    subtype_supertype,
    surrogate_key_stats,
)


# --------------------------------------------------------------------- #
# match_known_schema
# --------------------------------------------------------------------- #


def test_adventureworks_match():
    """The adv schema's anchor tables should produce a high-confidence
    AdventureWorks match."""
    tables = [
        "business_entity", "person", "customer", "employee", "vendor",
        "address", "country_region", "state_province",
        "business_entity_address", "credit_card", "person_credit_card",
        "sales_order_header", "sales_order_detail", "sales_person",
        "product", "product_subcategory", "product_category",
        "product_inventory", "purchase_order_header", "purchase_order_detail",
    ]
    out = match_known_schema(tables)
    assert out is not None
    assert out["name"] == "AdventureWorks"
    assert out["confidence"] >= 0.4


def test_northwind_match():
    """Generic Northwind tables (lowercase plural) match Northwind, not
    AdventureWorks (whose names are different)."""
    out = match_known_schema(["customers", "orders", "products", "suppliers", "employees"])
    assert out is not None
    assert out["name"] == "Northwind"


def test_no_match_below_threshold():
    """An unrelated table set returns None — no false-positive hits."""
    out = match_known_schema(
        ["my_widget", "my_gizmo", "my_thingamajig", "my_doohickey"],
    )
    assert out is None


def test_empty_input_returns_none():
    assert match_known_schema([]) is None


def test_match_carries_diff():
    """The returned dict includes missing + extra-sample so the UI can
    render a coverage diff."""
    out = match_known_schema(
        ["business_entity", "person", "customer"],  # tiny adv subset
        min_overlap=0.05,
    )
    assert out is not None
    assert "missing" in out and len(out["missing"]) > 0
    assert "extra_count" in out


# --------------------------------------------------------------------- #
# detect_temporal
# --------------------------------------------------------------------- #


def test_temporal_above_cdc_threshold():
    """80% of tables have modified_date → supports_cdc=True."""
    cols = [
        {"table": f"t{i}", "column": "modified_date"} for i in range(8)
    ] + [
        {"table": f"t{i}", "column": "name"} for i in range(10)
    ]
    out = detect_temporal(cols, total_tables=10)
    assert out["tracked_tables"] == 8
    assert out["fraction"] == 0.8
    assert out["supports_cdc"] is True


def test_temporal_below_threshold():
    cols = [
        {"table": "t0", "column": "modified_date"},
        {"table": "t1", "column": "name"},
        {"table": "t2", "column": "name"},
    ]
    out = detect_temporal(cols, total_tables=3)
    assert out["supports_cdc"] is False
    assert pytest.approx(out["fraction"], 0.01) == 0.3333


def test_temporal_recognises_alternate_names():
    """updated_at, last_modified, etc. all count."""
    cols = [
        {"table": "t1", "column": "updated_at"},
        {"table": "t2", "column": "last_modified"},
        {"table": "t3", "column": "date_modified"},
    ]
    out = detect_temporal(cols, total_tables=3)
    assert out["tracked_tables"] == 3


def test_temporal_does_not_match_substrings():
    """A column called modified_date_string shouldn't count — anchor
    is a full match, not a substring."""
    cols = [{"table": "t1", "column": "modified_date_string"}]
    out = detect_temporal(cols, total_tables=1)
    assert out["tracked_tables"] == 0


# --------------------------------------------------------------------- #
# surrogate_key_stats
# --------------------------------------------------------------------- #


def test_surrogate_pct_high_on_adv_like_pks():
    cols = [
        {"table": "t1", "column": "t1_id", "data_type": "integer", "is_pk": True},
        {"table": "t2", "column": "t2_id", "data_type": "bigint",  "is_pk": True},
        {"table": "t3", "column": "t3_id", "data_type": "serial",  "is_pk": True},
        {"table": "t4", "column": "code",  "data_type": "varchar", "is_pk": True},
    ]
    out = surrogate_key_stats(cols)
    assert out["tables_with_pk"] == 4
    assert out["surrogate_count"] == 3
    assert out["integer_count"] == 3


def test_surrogate_zero_when_no_pks():
    cols = [
        {"table": "t1", "column": "name", "data_type": "varchar", "is_pk": False},
    ]
    out = surrogate_key_stats(cols)
    assert out["tables_with_pk"] == 0
    assert out["surrogate_pct"] == 0


# --------------------------------------------------------------------- #
# bridge_tables
# --------------------------------------------------------------------- #


def test_bridge_table_detected():
    """A 3-col table with 2 FKs to different parents qualifies as a bridge."""
    cols = [
        {"table": "user_role", "column": "user_id"},
        {"table": "user_role", "column": "role_id"},
        {"table": "user_role", "column": "modified_date"},
    ]
    edges = [
        {"from": "user_role", "to": "user", "label": "user_id → user_id"},
        {"from": "user_role", "to": "role", "label": "role_id → role_id"},
    ]
    out = bridge_tables(cols, edges)
    assert len(out) == 1
    assert out[0]["table"] == "user_role"
    assert out[0]["parents"] == ["role", "user"]


def test_bridge_excludes_low_fk_ratio():
    """A 10-col table with 2 FKs is NOT a bridge (the FK columns are
    incidental, not the table's primary purpose).  After the tighten
    pass: bridge requires non-FK col count <= 2."""
    cols = [
        {"table": "big_table", "column": f"c{i}"} for i in range(10)
    ]
    edges = [
        {"from": "big_table", "to": "user", "label": "user_id → user_id"},
        {"from": "big_table", "to": "role", "label": "role_id → role_id"},
    ]
    out = bridge_tables(cols, edges)
    assert out == []


def test_bridge_excludes_fact_table_with_many_parents():
    """A small table with 8 FK parents is NOT a bridge — that's a
    fact / event / hub table.  Bridge cap is at 3 distinct parents."""
    cols = [{"table": "sales_order_header", "column": f"c{i}"} for i in range(10)]
    edges = [
        {"from": "sales_order_header", "to": p,
         "label": f"{p}_id → {p}_id"}
        for p in ["customer", "employee", "ship_method",
                  "currency_rate", "credit_card", "address",
                  "sales_person", "sales_territory"]
    ]
    out = bridge_tables(cols, edges)
    assert out == []


def test_bridge_requires_two_distinct_parents():
    """A table with 2 FKs to the SAME parent doesn't count as a bridge."""
    cols = [
        {"table": "log", "column": "from_user_id"},
        {"table": "log", "column": "to_user_id"},
        {"table": "log", "column": "ts"},
    ]
    edges = [
        {"from": "log", "to": "user", "label": "from_user_id → user_id"},
        {"from": "log", "to": "user", "label": "to_user_id → user_id"},
    ]
    out = bridge_tables(cols, edges)
    assert out == []  # Same parent twice → not a bridge


# --------------------------------------------------------------------- #
# subtype_supertype
# --------------------------------------------------------------------- #


def test_subtype_supertype_recognises_polymorphic_root():
    """Three children all FK-ing to business_entity_id → supertype hit."""
    edges = [
        {"from": "customer", "to": "business_entity",
         "label": "business_entity_id → business_entity_id"},
        {"from": "employee", "to": "business_entity",
         "label": "business_entity_id → business_entity_id"},
        {"from": "vendor",   "to": "business_entity",
         "label": "business_entity_id → business_entity_id"},
    ]
    out = subtype_supertype(edges)
    assert len(out) == 1
    assert out[0]["supertype"] == "business_entity"
    assert out[0]["fk_column"] == "business_entity_id"
    assert sorted(out[0]["subtypes"]) == ["customer", "employee", "vendor"]


def test_subtype_excludes_single_child():
    """A parent with only ONE child via that FK column is just a regular
    FK, not a polymorphic root."""
    edges = [
        {"from": "order_item", "to": "order",
         "label": "order_id → order_id"},
    ]
    assert subtype_supertype(edges) == []


def test_subtype_distinguishes_by_fk_column():
    """Different canonical FK columns to the same parent count as
    separate roots.  After the natural-key gate, only ``address_id``
    edges qualify; ``billing_address_id`` / ``shipping_address_id``
    are role-named columns that point to address but aren't the
    canonical name, so they're filtered out as polymorphic-root
    candidates (still surface in the relationships graph, just not
    in this insight)."""
    edges = [
        {"from": "order", "to": "address", "label": "address_id → address_id"},
        {"from": "user",  "to": "address", "label": "address_id → address_id"},
    ]
    out = subtype_supertype(edges)
    assert len(out) == 1
    assert out[0]["fk_column"] == "address_id"
    assert sorted(out[0]["subtypes"]) == ["order", "user"]


def test_subtype_excludes_fk_candidate_noise():
    """Same column matching multiple parent candidates (FK-candidate
    noise: business_entity_id matching both business_entity AND
    business_entity_address) shouldn't produce a polymorphic root for
    the non-canonical parent."""
    edges = [
        # Canonical: email_address.business_entity_id → business_entity
        {"from": "email_address", "to": "business_entity",
         "label": "business_entity_id → business_entity_id"},
        {"from": "employee", "to": "business_entity",
         "label": "business_entity_id → business_entity_id"},
        # Noise: same column, different parent (FK candidate that
        # validated by containment but isn't the canonical link).
        {"from": "email_address", "to": "business_entity_address",
         "label": "business_entity_id → business_entity_id"},
        {"from": "employee", "to": "business_entity_address",
         "label": "business_entity_id → business_entity_id"},
    ]
    out = subtype_supertype(edges)
    # Only the canonical business_entity root surfaces.
    assert len(out) == 1
    assert out[0]["supertype"] == "business_entity"
