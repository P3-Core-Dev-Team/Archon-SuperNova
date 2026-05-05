"""Smoke tests for discovery.jsonb_fk.

Covers the JSON walk, value-kind classification, DuckDB sampling, and the
end-to-end anti-join that joins JSON-extracted leaf values to a parent
column.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from discovery import jsonb_fk as jfk


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_value_kind_int():
    assert jfk._value_kind(1) == "int"
    assert jfk._value_kind(-42) == "int"
    assert jfk._value_kind("123") == "int"   # int-as-string
    assert jfk._value_kind("-5") == "int"
    assert jfk._value_kind(True) is None     # bools rejected
    assert jfk._value_kind(False) is None
    assert jfk._value_kind(1.5) is None       # floats rejected
    assert jfk._value_kind(None) is None


def test_value_kind_uuid():
    uid = "550e8400-e29b-41d4-a716-446655440000"
    assert jfk._value_kind(uid) == "uuid"


def test_value_kind_string():
    assert jfk._value_kind("hello") == "string"
    assert jfk._value_kind("") is None  # empty strings dropped


def test_extract_leaf_paths_flat_object():
    obj = {"a": 1, "b": {"c": 2, "d": 3}}
    out = sorted(jfk.extract_leaf_paths(obj))
    assert out == [("$.a", 1), ("$.b.c", 2), ("$.b.d", 3)]


def test_extract_leaf_paths_array():
    obj = {"items": [{"order_id": 1}, {"order_id": 2}]}
    out = list(jfk.extract_leaf_paths(obj))
    paths = [p for p, _ in out]
    assert "$.items[*].order_id" in paths


def test_path_supports_extract_skips_array_wildcards():
    assert jfk._path_supports_extract("$.order_id")
    assert jfk._path_supports_extract("$.actor.user_id")
    assert not jfk._path_supports_extract("$.items[*].order_id")


# ---------------------------------------------------------------------------
# DuckDB sampling + anti-join
# ---------------------------------------------------------------------------


def _write_parquet(path: Path, table: pa.Table) -> None:
    pq.write_table(table, path)


def test_sample_jsonb_paths_collects_int_paths(tmp_path: Path):
    """Walk a parquet file holding JSON text and discover the leaf paths."""
    payloads = [
        json.dumps({"order_id": 1, "user": {"id": 100}}),
        json.dumps({"order_id": 2, "user": {"id": 101}}),
        json.dumps({"order_id": 3, "user": {"id": 102}}),
    ]
    table = pa.table({"id": [1, 2, 3], "payload": payloads})
    p = tmp_path / "events.parquet"
    _write_parquet(p, table)

    con = duckdb.connect()
    paths = jfk._sample_jsonb_paths(con, p, "payload", sample_rows=10)
    # At least these two paths should be detected.
    assert "$.order_id" in paths
    assert "$.user.id" in paths
    # Both kinds are int.
    assert paths["$.order_id"] == "int"


def test_validate_jsonb_path_full_containment(tmp_path: Path):
    """child JSON ``$.order_id`` values fully contained in orders.id."""
    payloads = [
        json.dumps({"order_id": v}) for v in [1, 2, 3, 4, 5]
    ]
    events = pa.table({"id": [1, 2, 3, 4, 5], "payload": payloads})
    orders = pa.table({"id": [1, 2, 3, 4, 5, 6, 7]})

    ep = tmp_path / "events.parquet"
    op = tmp_path / "orders.parquet"
    _write_parquet(ep, events)
    _write_parquet(op, orders)

    con = duckdb.connect()
    cd, pd, orphans = jfk._validate_jsonb_path(
        con, ep, "payload", "$.order_id", op, "id"
    )
    assert cd == 5
    assert pd == 7
    assert orphans == 0


def test_validate_jsonb_path_partial_containment(tmp_path: Path):
    payloads = [json.dumps({"order_id": v}) for v in [1, 2, 999]]
    events = pa.table({"id": [1, 2, 3], "payload": payloads})
    orders = pa.table({"id": [1, 2, 3]})

    ep = tmp_path / "events.parquet"
    op = tmp_path / "orders.parquet"
    _write_parquet(ep, events)
    _write_parquet(op, orders)

    con = duckdb.connect()
    cd, pd, orphans = jfk._validate_jsonb_path(
        con, ep, "payload", "$.order_id", op, "id"
    )
    assert cd == 3
    assert pd == 3
    assert orphans == 1  # 999 is missing


def test_validate_jsonb_path_uuid_against_string_pk(tmp_path: Path):
    payloads = [
        json.dumps({"u": "550e8400-e29b-41d4-a716-446655440000"}),
        json.dumps({"u": "550e8400-e29b-41d4-a716-446655440001"}),
    ]
    events = pa.table({"id": [1, 2], "payload": payloads})
    users = pa.table({
        "id": [
            "550e8400-e29b-41d4-a716-446655440000",
            "550e8400-e29b-41d4-a716-446655440001",
            "550e8400-e29b-41d4-a716-446655440002",
        ]
    })

    ep = tmp_path / "events.parquet"
    up = tmp_path / "users.parquet"
    _write_parquet(ep, events)
    _write_parquet(up, users)

    con = duckdb.connect()
    cd, pd, orphans = jfk._validate_jsonb_path(
        con, ep, "payload", "$.u", up, "id"
    )
    assert cd == 2
    assert orphans == 0
