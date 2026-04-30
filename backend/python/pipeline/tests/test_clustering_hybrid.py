"""Tests for the hybrid clustering helpers added in Sprint 3.

Covers two pure functions in ``discovery.clustering`` that the rest of
the pipeline doesn't have any other way of testing without spinning up
the full SentenceTransformer model:

* ``_semantic_merge`` — runs after Louvain, optionally folds together
  communities that are semantically similar AND share inter-cluster
  FK edges, gated by a modularity floor.

* ``_zero_shot_label`` — assigns a business-domain label to a cluster
  based on the cosine similarity between the cluster's table-name
  centroid and a fixed vocabulary (``discovery.domain_vocab.DOMAINS``).

We monkey-patch ``discovery.name_similarity._embed`` so the tests run
without the actual model loaded — every test name maps to a small,
deterministic 3-d vector.
"""

from __future__ import annotations

from typing import Optional

import networkx as nx
import numpy as np
import pytest

from discovery import clustering, domain_vocab, name_similarity


def _make_embed(table: dict[str, np.ndarray]):
    """Return a fake _embed(name) that consults ``table`` and returns
    None for any unknown name."""

    def _fake(name: str) -> Optional[np.ndarray]:
        return table.get(name)

    return _fake


# --------------------------------------------------------------------- #
# _semantic_merge
# --------------------------------------------------------------------- #


def test_semantic_merge_combines_similar_clusters_when_connected(monkeypatch):
    """Two clusters with similar centroids AND a shared edge should merge."""
    # Two communities of two tables each. Within a community the
    # embeddings are identical; between communities they're close but
    # not identical.
    embeddings = {
        "customer":         np.array([1.0, 0.0, 0.0]),
        "customer_address": np.array([1.0, 0.05, 0.0]),
        "person":           np.array([0.95, 0.0, 0.0]),
        "person_phone":     np.array([0.93, 0.05, 0.0]),
    }
    monkeypatch.setattr(name_similarity, "_embed", _make_embed(embeddings))

    G = nx.Graph()
    G.add_edge(1, 2, weight=1.0)  # within community A
    G.add_edge(3, 4, weight=1.0)  # within community B
    G.add_edge(1, 3, weight=0.8)  # bridge between A and B (FK)

    table_by_id = {
        1: {"table_name": "customer"},
        2: {"table_name": "customer_address"},
        3: {"table_name": "person"},
        4: {"table_name": "person_phone"},
    }
    communities = [{1, 2}, {3, 4}]
    # Merging two well-separated communities ALWAYS drops modularity
    # toward zero (the merged cluster's L_c == 2m).  We use
    # modularity_floor=0.0 so the guard tolerates the drop and we can
    # confirm the threshold + edge logic actually fires; the
    # modularity_floor is exercised separately in
    # ``test_semantic_merge_modularity_guard_blocks_merge`` below.
    out = clustering._semantic_merge(
        communities, G, table_by_id,
        threshold=0.9,
        modularity_floor=0.0,
    )
    assert len(out) == 1
    assert out[0] == {1, 2, 3, 4}


def test_semantic_merge_modularity_guard_blocks_merge(monkeypatch):
    """A merge that would drop modularity below the floor is rejected."""
    embeddings = {
        "customer":         np.array([1.0, 0.0, 0.0]),
        "customer_address": np.array([1.0, 0.05, 0.0]),
        "person":           np.array([0.95, 0.0, 0.0]),
        "person_phone":     np.array([0.93, 0.05, 0.0]),
    }
    monkeypatch.setattr(name_similarity, "_embed", _make_embed(embeddings))

    G = nx.Graph()
    G.add_edge(1, 2, weight=1.0)
    G.add_edge(3, 4, weight=1.0)
    G.add_edge(1, 3, weight=0.8)
    table_by_id = {
        1: {"table_name": "customer"},
        2: {"table_name": "customer_address"},
        3: {"table_name": "person"},
        4: {"table_name": "person_phone"},
    }
    # Modularity floor of 0.95 means we'd need the post-merge
    # modularity to stay within 5% of base — impossible when the
    # merged cluster is the whole graph (modularity → 0).
    out = clustering._semantic_merge(
        [{1, 2}, {3, 4}], G, table_by_id,
        threshold=0.9,
        modularity_floor=0.95,
    )
    assert len(out) == 2  # untouched


def test_semantic_merge_skips_when_clusters_are_disconnected(monkeypatch):
    """No inter-cluster edge → no merge even when centroids are similar."""
    embeddings = {
        "a": np.array([1.0, 0.0, 0.0]),
        "b": np.array([1.0, 0.0, 0.0]),
        "c": np.array([1.0, 0.0, 0.0]),
        "d": np.array([1.0, 0.0, 0.0]),
    }
    monkeypatch.setattr(name_similarity, "_embed", _make_embed(embeddings))
    G = nx.Graph()
    G.add_edge(1, 2, weight=1.0)
    G.add_edge(3, 4, weight=1.0)  # NO edge from {1,2} to {3,4}
    table_by_id = {
        1: {"table_name": "a"}, 2: {"table_name": "b"},
        3: {"table_name": "c"}, 4: {"table_name": "d"},
    }
    out = clustering._semantic_merge(
        [{1, 2}, {3, 4}], G, table_by_id,
        threshold=0.5, modularity_floor=0.5,
    )
    assert len(out) == 2  # untouched


def test_semantic_merge_respects_threshold(monkeypatch):
    """Below-threshold similarity → no merge."""
    embeddings = {
        "alpha": np.array([1.0, 0.0, 0.0]),
        "beta":  np.array([0.0, 1.0, 0.0]),  # orthogonal → cos sim = 0
    }
    monkeypatch.setattr(name_similarity, "_embed", _make_embed(embeddings))
    G = nx.Graph()
    G.add_edge(1, 2, weight=1.0)  # bridge
    table_by_id = {
        1: {"table_name": "alpha"},
        2: {"table_name": "beta"},
    }
    out = clustering._semantic_merge(
        [{1}, {2}], G, table_by_id,
        threshold=0.5, modularity_floor=0.5,
    )
    assert len(out) == 2  # threshold rejects


def test_semantic_merge_no_op_when_embedder_unavailable(monkeypatch):
    """When _embed always returns None (no model), input is returned untouched."""
    monkeypatch.setattr(name_similarity, "_embed", lambda _: None)
    G = nx.Graph()
    G.add_edge(1, 2, weight=1.0)
    G.add_edge(1, 3, weight=1.0)
    table_by_id = {
        1: {"table_name": "x"},
        2: {"table_name": "y"},
        3: {"table_name": "z"},
    }
    out = clustering._semantic_merge(
        [{1, 2}, {3}], G, table_by_id,
        threshold=0.5, modularity_floor=0.5,
    )
    assert out == [{1, 2}, {3}]


def test_semantic_merge_threshold_zero_short_circuit(monkeypatch):
    """threshold <= 0 returns input directly without invoking the model."""
    called = {"n": 0}

    def _spy(_):
        called["n"] += 1
        return np.array([1.0, 0.0])

    monkeypatch.setattr(name_similarity, "_embed", _spy)
    out = clustering._semantic_merge(
        [{1}, {2}], nx.Graph(), {1: {"table_name": "a"}, 2: {"table_name": "b"}},
        threshold=0.0, modularity_floor=0.5,
    )
    assert out == [{1}, {2}]
    assert called["n"] == 0  # short-circuit before any embedding call


# --------------------------------------------------------------------- #
# _zero_shot_label
# --------------------------------------------------------------------- #


def test_zero_shot_label_picks_best_match(monkeypatch):
    """Cluster with sales-related tables should match the 'Sales' term."""
    # Map every test-relevant string to a deterministic vector. The
    # cluster's joined-name embedding is identical to the 'Sales'
    # search text embedding, so cosine similarity is 1.0.
    sales_text = next(text for label, text in domain_vocab.DOMAINS if label == "Sales")
    fake = {
        "sales_order sales_invoice customer": np.array([1.0, 0.0, 0.0]),
        sales_text:                            np.array([1.0, 0.0, 0.0]),
    }
    # All other vocabulary terms get an orthogonal embedding so they
    # don't accidentally win the comparison.
    for label, text in domain_vocab.DOMAINS:
        fake.setdefault(text, np.array([0.0, 1.0, 0.0]))

    monkeypatch.setattr(name_similarity, "_embed", _make_embed(fake))

    label = clustering._zero_shot_label(
        [
            {"table_name": "sales_order"},
            {"table_name": "sales_invoice"},
            {"table_name": "customer"},
        ],
        threshold=0.5,
    )
    assert label == "Sales"


def test_zero_shot_label_below_threshold_returns_none(monkeypatch):
    """No vocabulary term exceeds the threshold → returns None."""
    # Cluster centroid orthogonal to every domain term.
    fake = {"alpha beta": np.array([1.0, 0.0])}
    for _label, text in domain_vocab.DOMAINS:
        fake.setdefault(text, np.array([0.0, 1.0]))
    monkeypatch.setattr(name_similarity, "_embed", _make_embed(fake))
    out = clustering._zero_shot_label(
        [{"table_name": "alpha"}, {"table_name": "beta"}],
        threshold=0.5,
    )
    assert out is None


def test_zero_shot_label_returns_none_when_model_unavailable(monkeypatch):
    monkeypatch.setattr(name_similarity, "_embed", lambda _: None)
    out = clustering._zero_shot_label(
        [{"table_name": "anything"}],
        threshold=0.5,
    )
    assert out is None


def test_zero_shot_label_handles_empty_cluster():
    """Empty member_tables → None, never raise."""
    assert clustering._zero_shot_label([], threshold=0.5) is None


def test_zero_shot_label_threshold_zero_short_circuit(monkeypatch):
    """threshold <= 0 short-circuits before any embedding call."""
    called = {"n": 0}

    def _spy(_):
        called["n"] += 1
        return np.array([1.0, 0.0])

    monkeypatch.setattr(name_similarity, "_embed", _spy)
    assert clustering._zero_shot_label(
        [{"table_name": "x"}], threshold=0.0,
    ) is None
    assert called["n"] == 0
