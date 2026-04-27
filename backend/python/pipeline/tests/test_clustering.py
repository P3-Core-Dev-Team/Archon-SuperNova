"""
test_clustering.py - Unit tests for ``discovery.clustering``.

Covers:
  * Archetype tagging (FACT / DIMENSION / LOOKUP / JUNCTION / AUDIT).
  * Junction-collapse edge synthesis.
  * Determinism of weighted Louvain.
  * Cluster naming cascade (schema / anchor / lexical-prefix / fallback).
  * PII bonus tightening clusters.

These tests are pure: no DB, no Docker, no testcontainers fixtures.  They run
on any laptop that has ``networkx`` available.
"""
from __future__ import annotations

import pytest

from discovery.clustering import (
    Cluster,
    ClusteredTable,
    ClusteringResult,
    cluster_schema,
)


# ---------------------------------------------------------------------------
# Synthetic-data builders
# ---------------------------------------------------------------------------


def _mk_table(
    tid: int,
    name: str,
    *,
    schema: str = "public",
    rows: int = 1000,
) -> dict:
    return {
        "table_id": tid,
        "schema_name": schema,
        "table_name": name,
        "row_count_estimate": rows,
    }


def _mk_col(
    cid: int,
    tid: int,
    name: str,
    *,
    is_pk: bool = False,
    is_implicit_pk: bool = False,
) -> dict:
    return {
        "column_id": cid,
        "table_id": tid,
        "column_name": name,
        "is_pk": is_pk,
        "is_implicit_pk": is_implicit_pk,
    }


def _mk_edge(
    child_col_id: int,
    parent_col_id: int,
    *,
    cardinality: str = "MANY_TO_ONE",
    confidence: float = 0.95,
) -> dict:
    return {
        "child_col_id": child_col_id,
        "parent_col_id": parent_col_id,
        "cardinality": cardinality,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# 1. Typology - FACT / DIMENSION / LOOKUP
# ---------------------------------------------------------------------------


def test_typology_simple_fact_dim_lookup():
    """A 5-table mini-warehouse should yield the expected archetype mix."""
    # Anchor FACT 'sales' (large, fans into 4 dims) plus a small lookup
    # 'currency' that 4 tables FK into.  No junction.
    tables = [
        _mk_table(1, "sales", rows=500_000),
        _mk_table(2, "customers", rows=2_000),
        _mk_table(3, "products", rows=1_500),
        _mk_table(4, "stores", rows=200),
        _mk_table(5, "currency", rows=20),
    ]
    columns = [
        # sales: id, customer_id, product_id, store_id, currency_id, amount
        _mk_col(10, 1, "id", is_pk=True),
        _mk_col(11, 1, "customer_id"),
        _mk_col(12, 1, "product_id"),
        _mk_col(13, 1, "store_id"),
        _mk_col(14, 1, "currency_id"),
        _mk_col(15, 1, "amount"),
        # customers: id, currency_id (so currency has 4 inbound)
        _mk_col(20, 2, "id", is_pk=True),
        _mk_col(21, 2, "currency_id"),
        # products: id, currency_id
        _mk_col(30, 3, "id", is_pk=True),
        _mk_col(31, 3, "currency_id"),
        # stores: id, currency_id
        _mk_col(40, 4, "id", is_pk=True),
        _mk_col(41, 4, "currency_id"),
        # currency: id, code
        _mk_col(50, 5, "id", is_pk=True),
        _mk_col(51, 5, "code"),
    ]
    edges = [
        _mk_edge(11, 20),  # sales -> customers
        _mk_edge(12, 30),  # sales -> products
        _mk_edge(13, 40),  # sales -> stores
        _mk_edge(14, 50),  # sales -> currency
        _mk_edge(21, 50),  # customers -> currency
        _mk_edge(31, 50),  # products -> currency
        _mk_edge(41, 50),  # stores -> currency
    ]
    result = cluster_schema(
        "public",
        tables=tables,
        columns=columns,
        edges=edges,
        pii_findings=[],
    )
    arch_by_tid = {a.table_id: a.archetype for a in result.table_assignments}
    assert arch_by_tid[1] == "FACT", f"sales should be FACT, got {arch_by_tid[1]}"
    assert arch_by_tid[5] == "LOOKUP", (
        f"currency should be LOOKUP (rows<100, 4 inbound, no outbound), "
        f"got {arch_by_tid[5]}"
    )
    # The remaining tables can land as DIMENSION or FACT depending on
    # quartile cutoffs; only require *something* sensible.
    assert all(
        arch_by_tid[t] in {"FACT", "DIMENSION"}
        for t in (2, 3, 4)
    ), arch_by_tid


# ---------------------------------------------------------------------------
# 2. Junction collapse emits MTM edge
# ---------------------------------------------------------------------------


def test_junction_collapse_emits_mtm_edge():
    """A->J<-B (J=junction with id+order_id+product_id) collapses to A<->B."""
    tables = [
        _mk_table(1, "orders", rows=10_000),
        _mk_table(2, "order_items", rows=80_000),
        _mk_table(3, "products", rows=500),
    ]
    columns = [
        _mk_col(10, 1, "id", is_pk=True),
        _mk_col(11, 1, "ts"),
        _mk_col(20, 2, "id", is_pk=True),
        _mk_col(21, 2, "order_id"),
        _mk_col(22, 2, "product_id"),
        _mk_col(30, 3, "id", is_pk=True),
        _mk_col(31, 3, "name"),
    ]
    edges = [
        _mk_edge(21, 10),  # order_items -> orders
        _mk_edge(22, 30),  # order_items -> products
    ]
    result = cluster_schema(
        "public",
        tables=tables,
        columns=columns,
        edges=edges,
        pii_findings=[],
    )
    # Junction must be tagged + collapsed.
    assert 2 in result.junction_collapsed
    arch_by_tid = {a.table_id: a.archetype for a in result.table_assignments}
    assert arch_by_tid[2] == "JUNCTION"
    # Post-collapse there must be exactly ONE edge between orders (1) and
    # products (3) - the synthetic M:M.
    assert result.edge_count_post_collapse == 1
    # Junction's cluster must equal its dominant parent's cluster - both
    # orders and products land in the same community after collapse.
    cluster_of = {a.table_id: a.cluster_id for a in result.table_assignments}
    assert cluster_of[2] in (cluster_of[1], cluster_of[3])
    assert cluster_of[1] == cluster_of[3], (
        "After collapse the M:M edge should pull orders + products "
        "into the same cluster"
    )


# ---------------------------------------------------------------------------
# 3. Determinism
# ---------------------------------------------------------------------------


def test_weighted_louvain_deterministic():
    """Same input -> same clusters across two calls (seed=42)."""
    tables = [_mk_table(i, f"tbl_{i}", rows=1000 + i * 100) for i in range(1, 11)]
    columns = []
    cid = 100
    for t in tables:
        columns.append(_mk_col(cid, t["table_id"], "id", is_pk=True))
        columns.append(_mk_col(cid + 1, t["table_id"], "ref"))
        cid += 10
    # Connect a chain plus a couple of cross-links.
    edges = []
    for i in range(1, 10):
        # Link tbl_i.ref (col_id = 100 + (i-1)*10 + 1) -> tbl_{i+1}.id (col_id = 100 + i*10)
        child = 100 + (i - 1) * 10 + 1
        parent = 100 + i * 10
        edges.append(_mk_edge(child, parent))
    # Cross-link 3 -> 7 to break a perfect chain.
    edges.append(_mk_edge(100 + 2 * 10 + 1, 100 + 6 * 10))

    r1 = cluster_schema(
        "public", tables=tables, columns=columns, edges=edges, pii_findings=[]
    )
    r2 = cluster_schema(
        "public", tables=tables, columns=columns, edges=edges, pii_findings=[]
    )

    assert r1.modularity_score == r2.modularity_score
    a1 = sorted(((a.table_id, a.cluster_id) for a in r1.table_assignments))
    a2 = sorted(((a.table_id, a.cluster_id) for a in r2.table_assignments))
    assert a1 == a2
    n1 = sorted((c.cluster_id, c.name, tuple(c.table_ids)) for c in r1.clusters)
    n2 = sorted((c.cluster_id, c.name, tuple(c.table_ids)) for c in r2.clusters)
    assert n1 == n2
    # Per-cluster modularity contributions must sum to the total score.
    sum_q = sum(c.modularity_contribution for c in r1.clusters)
    assert abs(sum_q - r1.modularity_score) < 1e-9, (
        f"per-cluster Qs ({sum_q}) must sum to total modularity ({r1.modularity_score})"
    )


# ---------------------------------------------------------------------------
# 4. Naming - schema priority
# ---------------------------------------------------------------------------


def test_naming_schema_priority():
    """All tables in schema 'hr' -> cluster name 'hr' (Rule 1)."""
    tables = [
        _mk_table(1, "employees", schema="hr", rows=200),
        _mk_table(2, "departments", schema="hr", rows=10),
        _mk_table(3, "salaries", schema="hr", rows=2000),
    ]
    columns = [
        _mk_col(10, 1, "id", is_pk=True),
        _mk_col(11, 1, "dept_id"),
        _mk_col(20, 2, "id", is_pk=True),
        _mk_col(30, 3, "id", is_pk=True),
        _mk_col(31, 3, "emp_id"),
    ]
    edges = [_mk_edge(11, 20), _mk_edge(31, 10)]
    result = cluster_schema(
        "hr", tables=tables, columns=columns, edges=edges, pii_findings=[]
    )
    # Every cluster's name should be exactly 'hr' (Rule 1 wins).
    assert result.clusters, "should have at least one cluster"
    for c in result.clusters:
        assert c.name == "hr", f"expected schema-name, got {c.name!r}"
        assert c.schema_name == "hr"


# ---------------------------------------------------------------------------
# 5. Naming - lexical prefix (forces Rule 3 by using schema='public')
# ---------------------------------------------------------------------------


def test_naming_lexical_prefix():
    """When schema is public and 60%+ tables share token-1 -> '<token>_cluster'."""
    # Five tables in schema 'public', four start with 'order_' -> 80% share
    # token-1.  Rule 1 doesn't fire (public); Rule 2 might pick a FACT/DIM
    # anchor whose singularised name is 'order_*', so Rule 2 may also yield
    # 'order_<...>_cluster'.  We accept either as long as it starts 'order'.
    tables = [
        _mk_table(1, "order_header", rows=10_000),
        _mk_table(2, "order_line",   rows=50_000),
        _mk_table(3, "order_status", rows=10),
        _mk_table(4, "order_event",  rows=200_000),
        _mk_table(5, "users",        rows=100),
    ]
    columns = [
        _mk_col(10, 1, "id", is_pk=True),
        _mk_col(11, 1, "user_id"),
        _mk_col(20, 2, "id", is_pk=True),
        _mk_col(21, 2, "header_id"),
        _mk_col(30, 3, "id", is_pk=True),
        _mk_col(40, 4, "id", is_pk=True),
        _mk_col(41, 4, "header_id"),
        _mk_col(50, 5, "id", is_pk=True),
    ]
    edges = [
        _mk_edge(11, 50),  # order_header.user_id -> users.id
        _mk_edge(21, 10),  # order_line.header_id -> order_header.id
        _mk_edge(41, 10),  # order_event.header_id -> order_header.id
    ]
    result = cluster_schema(
        "public", tables=tables, columns=columns, edges=edges, pii_findings=[]
    )
    # Cluster containing the order_* tables must have a name beginning with
    # 'order' (either Rule-2 anchor or Rule-3 lexical-prefix).
    cluster_of = {a.table_id: a.cluster_id for a in result.table_assignments}
    by_id = {c.cluster_id: c for c in result.clusters}
    order_cluster = by_id[cluster_of[1]]
    assert order_cluster.name.startswith("order"), (
        f"expected order-anchored name, got {order_cluster.name!r}"
    )


def test_naming_internal_rule_3_lexical_prefix():
    """Rule 3 unit-test via the internal helper.

    Exercising Rule 3 end-to-end is brittle because the archetype tagger
    almost always classifies at least one cluster member as FACT or
    DIMENSION (Rule 2 then dominates).  We therefore unit-test the
    naming helper directly with a synthetic cluster whose archetypes are
    all 'JUNCTION' (no FACT / DIMENSION anchor) and whose member table
    names share the 'audit_' token-1 prefix.
    """
    from discovery.clustering import _name_cluster
    member_tables = [
        {"table_id": 11, "schema_name": "public", "table_name": "audit_a"},
        {"table_id": 12, "schema_name": "public", "table_name": "audit_b"},
        {"table_id": 13, "schema_name": "public", "table_name": "audit_c"},
        {"table_id": 14, "schema_name": "public", "table_name": "ledger"},
    ]
    archetypes = {11: "JUNCTION", 12: "JUNCTION", 13: "JUNCTION", 14: "JUNCTION"}
    weighted_degree = {11: 1.0, 12: 1.0, 13: 1.0, 14: 1.0}
    name, _schema = _name_cluster(
        cluster_id=7,
        member_tables=member_tables,
        schema_name="public",
        archetypes=archetypes,
        weighted_degree=weighted_degree,
    )
    # 3/4 = 75% share token-1 'audit' -> Rule 3 fires.
    assert name == "audit_cluster", f"expected 'audit_cluster', got {name!r}"


def test_naming_internal_rule_4_fallback():
    """Rule 4 fallback: no schema bias, no anchor, no >=60% prefix."""
    from discovery.clustering import _name_cluster
    member_tables = [
        {"table_id": 21, "schema_name": "public", "table_name": "alpha"},
        {"table_id": 22, "schema_name": "public", "table_name": "beta"},
        {"table_id": 23, "schema_name": "public", "table_name": "gamma"},
    ]
    archetypes = {21: "JUNCTION", 22: "JUNCTION", 23: "JUNCTION"}
    weighted_degree = {21: 0.0, 22: 0.0, 23: 0.0}
    name, _ = _name_cluster(
        cluster_id=42,
        member_tables=member_tables,
        schema_name="public",
        archetypes=archetypes,
        weighted_degree=weighted_degree,
    )
    assert name == "cluster_42", f"expected fallback 'cluster_42', got {name!r}"


# ---------------------------------------------------------------------------
# 6. PII bonus tightens cluster
# ---------------------------------------------------------------------------


def test_pii_bonus_pulls_pii_tables_together():
    """Two endpoints sharing a PII type get +0.10 weight per FK column-edge,
    which is enough to merge two communities that otherwise sit apart.

    Construction:
      * Two K3 cliques: {1,2,3} and {4,5,6}; every intra-clique edge has
        confidence 0.95 (=> per-edge weight 1.10).
      * A *two-column* bridge between table 3 and table 4 (e.g. two FK
        columns).  Each bridge column-edge has confidence 0.85
        (=> per-edge weight 1.00 without PII, 1.10 with PII).
      * Sum-of-weights at the projected table level: bridge = 2.00 without
        PII, 2.20 with PII.  That nudge is enough to flip the Louvain
        assignment of (3, 4) from "different cluster" to "same cluster."
    """
    tables = [_mk_table(i, f"t{i}", rows=1000) for i in range(1, 7)]
    columns = []
    cid = 100
    for tid in range(1, 7):
        columns.append(_mk_col(cid, tid, "id", is_pk=True))
        for k in range(1, 10):
            columns.append(_mk_col(cid + k, tid, f"ref{k}"))
        cid += 20
    edges = [
        # K3 on {1,2,3}
        _mk_edge(101, 120),  # 1 -> 2
        _mk_edge(121, 140),  # 2 -> 3
        _mk_edge(141, 100),  # 3 -> 1
        # K3 on {4,5,6}
        _mk_edge(161, 180),  # 4 -> 5
        _mk_edge(181, 200),  # 5 -> 6
        _mk_edge(201, 160),  # 6 -> 4
        # Two-column bridge 3 -> 4
        _mk_edge(142, 160, confidence=0.85),
        _mk_edge(143, 160, confidence=0.85),
    ]
    pii_share = [
        # Bridge endpoints share EMAIL -> +0.10 per column-edge.
        {"table_id": 3, "pii_type": "EMAIL"},
        {"table_id": 4, "pii_type": "EMAIL"},
    ]

    r_no_pii = cluster_schema(
        "public", tables=tables, columns=columns, edges=edges, pii_findings=[]
    )
    r_with_pii = cluster_schema(
        "public",
        tables=tables,
        columns=columns,
        edges=edges,
        pii_findings=pii_share,
    )

    # Without bonus: tables 3 and 4 must be in different clusters.
    assn_no = {a.table_id: a.cluster_id for a in r_no_pii.table_assignments}
    assert assn_no[3] != assn_no[4], (
        f"baseline should keep 3 and 4 separate; clusters="
        f"{[c.table_ids for c in r_no_pii.clusters]}"
    )

    # With bonus: tables 3 and 4 must collapse into the same cluster.
    assn_with = {a.table_id: a.cluster_id for a in r_with_pii.table_assignments}
    assert assn_with[3] == assn_with[4], (
        f"PII bonus should pull 3 and 4 into the same cluster - got "
        f"{assn_with[3]} vs {assn_with[4]}; clusters="
        f"{[c.table_ids for c in r_with_pii.clusters]}"
    )


# ---------------------------------------------------------------------------
# 7. Empty-input safety
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_result():
    """Passing zero tables yields an empty ClusteringResult."""
    result = cluster_schema(
        "public", tables=[], columns=[], edges=[], pii_findings=[]
    )
    assert isinstance(result, ClusteringResult)
    assert result.clusters == ()
    assert result.table_assignments == ()
    assert result.junction_collapsed == ()
    assert result.modularity_score == 0.0
    assert result.edge_count_post_collapse == 0


# ---------------------------------------------------------------------------
# 8. Confidence floor drops weak edges
# ---------------------------------------------------------------------------


def test_confidence_floor_drops_low_conf_edges():
    """Edges with confidence < confidence_floor must be ignored."""
    tables = [_mk_table(1, "a"), _mk_table(2, "b")]
    columns = [
        _mk_col(10, 1, "id", is_pk=True),
        _mk_col(11, 1, "ref"),
        _mk_col(20, 2, "id", is_pk=True),
    ]
    # Below floor.
    edges = [_mk_edge(11, 20, confidence=0.5)]
    r = cluster_schema(
        "public",
        tables=tables,
        columns=columns,
        edges=edges,
        pii_findings=[],
        confidence_floor=0.7,
    )
    # No edges -> two singleton clusters.
    assert r.edge_count_post_collapse == 0
    cids = {a.cluster_id for a in r.table_assignments}
    assert len(cids) == 2
