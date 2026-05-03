"""Smoke tests for discovery.polymorphic_fk -- focused on the pure
helpers + the DuckDB partition validator (no Postgres / SQLAlchemy).
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from discovery import polymorphic_fk as pfk


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_split_type_prefix_basic():
    assert pfk._split_type_prefix("commentable_type") == "commentable"
    assert pfk._split_type_prefix("attachable_kind") == "attachable"
    assert pfk._split_type_prefix("user_id") is None
    assert pfk._split_type_prefix("type") is None


def test_singularize_handles_common_suffixes():
    # Naive ``_es`` rule strips unconditionally, so "articles" rounds to
    # "articl" -- documented behaviour, not a bug; the candidate-name
    # generator below tries all common forms to compensate.
    assert pfk._singularize("posts") == "post"
    assert pfk._singularize("categories") == "category"
    # 'class' ends with 'ss' so the trailing 's' is NOT stripped.
    assert pfk._singularize("class") == "class"
    # Plural-ize handles the y -> ies and consonant-ies rules for the
    # forms users actually need.
    assert pfk._pluralize("post") == "posts"
    assert pfk._pluralize("category") == "categories"


def test_candidate_parent_names_covers_singular_and_plural():
    """The candidate generator MUST emit both 'post' and 'posts' so the
    matcher works regardless of whether the parent table is named 'post'
    or 'posts' in the source DB.
    """
    cands = pfk._candidate_parent_names("Post")
    assert "post" in cands and "posts" in cands
    cands = pfk._candidate_parent_names("articles")
    assert "article" in cands or "articles" in cands


def test_candidate_parent_names():
    cands = pfk._candidate_parent_names("Post")
    assert "post" in cands
    assert "posts" in cands

    cands = pfk._candidate_parent_names("orders")
    assert "order" in cands
    assert "orders" in cands


def test_parent_name_match_plural_aware():
    assert pfk._parent_name_match("Post", "posts")
    assert pfk._parent_name_match("Article", "articles")
    assert pfk._parent_name_match("Category", "categories")
    assert pfk._parent_name_match("orders", "Orders")
    assert not pfk._parent_name_match("Comment", "posts")


# ---------------------------------------------------------------------------
# DuckDB partitioned anti-join
# ---------------------------------------------------------------------------


def _write_parquet(path: Path, table: pa.Table) -> None:
    pq.write_table(table, path)


def test_validate_partition_full_containment(tmp_path: Path):
    """A discriminator partition that fully matches a parent column."""
    # comments(commentable_type, commentable_id, body)
    child = pa.table(
        {
            "commentable_type": ["Post", "Post", "Article", "Article", "Post"],
            "commentable_id": [1, 2, 10, 11, 3],
            "body": ["x", "y", "z", "a", "b"],
        }
    )
    posts = pa.table(
        {"id": [1, 2, 3, 4, 5], "title": ["t1", "t2", "t3", "t4", "t5"]}
    )

    child_path = tmp_path / "comments.parquet"
    posts_path = tmp_path / "posts.parquet"
    _write_parquet(child_path, child)
    _write_parquet(posts_path, posts)

    con = duckdb.connect()
    cd, pd, orphans = pfk._validate_partition(
        con, child_path, "commentable_type", "commentable_id",
        "Post", posts_path, "id",
    )
    # 3 child rows where type='Post' (ids 1,2,3 -> 3 distinct).
    assert cd == 3
    assert pd == 5
    assert orphans == 0


def test_validate_partition_partial_containment(tmp_path: Path):
    """A partition whose ids are partly missing from the candidate parent."""
    child = pa.table(
        {
            "commentable_type": ["Post", "Post", "Post"],
            "commentable_id": [1, 2, 999],
            "body": ["x", "y", "z"],
        }
    )
    posts = pa.table({"id": [1, 2, 3], "title": ["t1", "t2", "t3"]})

    cp = tmp_path / "c.parquet"
    pp = tmp_path / "p.parquet"
    _write_parquet(cp, child)
    _write_parquet(pp, posts)

    con = duckdb.connect()
    cd, pd, orphans = pfk._validate_partition(
        con, cp, "commentable_type", "commentable_id",
        "Post", pp, "id",
    )
    assert cd == 3  # 1, 2, 999
    assert pd == 3  # 1, 2, 3
    assert orphans == 1  # 999 is not in posts


def test_validate_partition_wrong_parent_no_overlap(tmp_path: Path):
    """When the parent has none of the discriminated ids, orphans == cd."""
    child = pa.table(
        {
            "commentable_type": ["Article", "Article"],
            "commentable_id": [10, 11],
        }
    )
    # posts.id values don't overlap with the article ids
    posts = pa.table({"id": [1, 2, 3]})

    cp = tmp_path / "c.parquet"
    pp = tmp_path / "p.parquet"
    _write_parquet(cp, child)
    _write_parquet(pp, posts)

    con = duckdb.connect()
    cd, pd, orphans = pfk._validate_partition(
        con, cp, "commentable_type", "commentable_id",
        "Article", pp, "id",
    )
    assert cd == 2
    assert pd == 3
    assert orphans == 2  # neither 10 nor 11 is in posts


def test_list_distinct_values(tmp_path: Path):
    child = pa.table(
        {
            "commentable_type": ["Post", "Post", "Article", "Article", "Post"],
        }
    )
    cp = tmp_path / "c.parquet"
    _write_parquet(cp, child)
    con = duckdb.connect()
    values = pfk._list_distinct_values(con, cp, "commentable_type", 20)
    assert sorted(values) == ["Article", "Post"]
