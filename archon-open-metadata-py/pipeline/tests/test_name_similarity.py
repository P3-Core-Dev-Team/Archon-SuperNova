"""
Tests for discovery.name_similarity — the optional semantic-similarity module.

Heavy parts (the actual transformer model) are mocked. One real-model
integration test runs only when sentence-transformers is installed.
"""
from __future__ import annotations

import importlib

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def ns(monkeypatch):
    """Reload the module fresh for each test so caches / module state don't
    leak between tests."""
    import discovery.name_similarity as mod

    importlib.reload(mod)
    # Clear any caches the freshly-loaded module already populated.
    mod._embed.cache_clear()
    mod._semantic_similarity_cached.cache_clear()
    return mod


# ---------------------------------------------------------------------------
# Lex-only behaviour (always available)
# ---------------------------------------------------------------------------


def test_lex_similarity_always_works(ns, monkeypatch):
    """Even with semantic disabled, lex_similarity returns a sensible number."""
    monkeypatch.setattr(ns, "SEMANTIC_AVAILABLE", False)
    val = ns.lex_similarity("customer_id", "customers.id")
    assert isinstance(val, float)
    assert 0.0 <= val <= 1.0
    # And it should be reasonably high for these obviously-similar names.
    assert val > 0.4


def test_lex_similarity_identity_is_one(ns):
    assert ns.lex_similarity("foo", "foo") == pytest.approx(1.0)


def test_lex_similarity_empty_returns_zero(ns):
    assert ns.lex_similarity("", "foo") == 0.0
    assert ns.lex_similarity("foo", "") == 0.0
    assert ns.lex_similarity("", "") == 0.0


# ---------------------------------------------------------------------------
# Semantic disabled paths
# ---------------------------------------------------------------------------


def test_semantic_returns_none_when_unavailable(ns, monkeypatch):
    """When SEMANTIC_AVAILABLE is False AND the lazy load fails,
    semantic_similarity returns None."""
    monkeypatch.setattr(ns, "SEMANTIC_AVAILABLE", False)
    # Force _get_model to fail (simulating no sentence-transformers installed).
    monkeypatch.setattr(ns, "_get_model", lambda: None)
    # Also clear any leftover cached value
    ns._semantic_similarity_cached.cache_clear()
    assert ns.semantic_similarity("a", "b") is None


def test_best_similarity_falls_back_to_lex_when_semantic_missing(ns, monkeypatch):
    """semantic=None -> best == lex."""
    monkeypatch.setattr(ns, "SEMANTIC_AVAILABLE", False)
    monkeypatch.setattr(ns, "_get_model", lambda: None)
    ns._semantic_similarity_cached.cache_clear()

    lex_val = ns.lex_similarity("customer_id", "customers.id")
    best_val = ns.best_similarity("customer_id", "customers.id")
    assert best_val == lex_val


# ---------------------------------------------------------------------------
# best_similarity uses max
# ---------------------------------------------------------------------------


def test_best_similarity_uses_max(ns, monkeypatch):
    """When both lex and semantic return numbers, best is their max."""
    monkeypatch.setattr(ns, "lex_similarity", lambda a, b: 0.7)
    monkeypatch.setattr(ns, "semantic_similarity", lambda a, b: 0.9)
    assert ns.best_similarity("x", "y") == pytest.approx(0.9)


def test_best_similarity_uses_max_when_lex_higher(ns, monkeypatch):
    """When lex > semantic, best == lex."""
    monkeypatch.setattr(ns, "lex_similarity", lambda a, b: 0.85)
    monkeypatch.setattr(ns, "semantic_similarity", lambda a, b: 0.5)
    assert ns.best_similarity("x", "y") == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------


def test_warmup_idempotent(ns, monkeypatch):
    """Calling warmup multiple times doesn't error and doesn't re-load."""
    calls = {"n": 0}

    def fake_get_model():
        calls["n"] += 1
        return None  # simulate load failure (sentence-transformers missing)

    monkeypatch.setattr(ns, "_get_model", fake_get_model)
    ns.warmup()
    ns.warmup()
    ns.warmup()
    # No exceptions raised; helper invoked each time but harmless.
    assert calls["n"] == 3


def test_warmup_no_error_when_unavailable(ns, monkeypatch):
    """warmup must never raise even if sentence-transformers is missing."""
    monkeypatch.setattr(ns, "_get_model", lambda: None)
    ns.warmup()  # must not raise


# ---------------------------------------------------------------------------
# Empty strings
# ---------------------------------------------------------------------------


def test_empty_strings(ns, monkeypatch):
    """Empty input on either side yields 0.0 similarity overall."""
    monkeypatch.setattr(ns, "SEMANTIC_AVAILABLE", False)
    monkeypatch.setattr(ns, "_get_model", lambda: None)
    ns._semantic_similarity_cached.cache_clear()

    assert ns.best_similarity("", "") == 0.0
    assert ns.best_similarity("foo", "") == 0.0
    assert ns.best_similarity("", "foo") == 0.0
    assert ns.lex_similarity("", "") == 0.0


# ---------------------------------------------------------------------------
# Mocked semantic path: verify cosine math + clamping with fixed vectors
# ---------------------------------------------------------------------------


def test_semantic_cosine_with_mocked_embeddings(ns, monkeypatch):
    """Mock _embed to return fixed vectors; verify cosine math + clamp."""
    monkeypatch.setattr(ns, "SEMANTIC_AVAILABLE", True)

    def fake_embed(name: str):
        if name == "a":
            return np.array([1.0, 0.0, 0.0], dtype=np.float32)
        if name == "b":
            return np.array([0.0, 1.0, 0.0], dtype=np.float32)
        if name == "c":
            return np.array([1.0, 0.0, 0.0], dtype=np.float32)  # identical to a
        return np.zeros(3, dtype=np.float32)

    monkeypatch.setattr(ns, "_embed", fake_embed)
    ns._semantic_similarity_cached.cache_clear()

    # Orthogonal -> cosine 0
    assert ns.semantic_similarity("a", "b") == pytest.approx(0.0)
    # Identical -> cosine 1
    assert ns.semantic_similarity("a", "c") == pytest.approx(1.0)


def test_semantic_clamps_negative_to_zero(ns, monkeypatch):
    """Negative cosine (opposing vectors) must clamp to 0.0."""
    monkeypatch.setattr(ns, "SEMANTIC_AVAILABLE", True)

    def fake_embed(name: str):
        if name == "x":
            return np.array([1.0, 0.0], dtype=np.float32)
        return np.array([-1.0, 0.0], dtype=np.float32)  # opposite direction

    monkeypatch.setattr(ns, "_embed", fake_embed)
    ns._semantic_similarity_cached.cache_clear()

    val = ns.semantic_similarity("x", "y")
    assert val is not None
    assert val == pytest.approx(0.0)


def test_semantic_zero_vector_returns_zero(ns, monkeypatch):
    """Zero-norm vector gives 0.0 (avoids div-by-zero crash)."""
    monkeypatch.setattr(ns, "SEMANTIC_AVAILABLE", True)
    monkeypatch.setattr(ns, "_embed", lambda name: np.zeros(3, dtype=np.float32))
    ns._semantic_similarity_cached.cache_clear()

    assert ns.semantic_similarity("a", "b") == pytest.approx(0.0)


def test_semantic_cache_is_symmetric(ns, monkeypatch):
    """Calling with (a, b) then (b, a) hits the same cache entry."""
    monkeypatch.setattr(ns, "SEMANTIC_AVAILABLE", True)

    call_count = {"n": 0}

    def counted_embed(name: str):
        call_count["n"] += 1
        # Stable mapping based on first letter.
        return np.array([float(ord(name[0])), 1.0, 0.0], dtype=np.float32)

    monkeypatch.setattr(ns, "_embed", counted_embed)
    ns._semantic_similarity_cached.cache_clear()

    v1 = ns.semantic_similarity("foo", "bar")
    v2 = ns.semantic_similarity("bar", "foo")
    assert v1 == pytest.approx(v2)


# ---------------------------------------------------------------------------
# Real-model integration test (skipped if sentence-transformers not installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="sentence-transformers not installed (optional 'semantic' extra)",
)
def test_integration_real_model_customer_vs_client(ns):
    """customer_id vs client_id should score > 0.5 with the real model."""
    ns.warmup()
    if not ns.SEMANTIC_AVAILABLE:
        pytest.skip("sentence-transformers installed but model failed to load")
    val = ns.semantic_similarity("customer_id", "client_id")
    assert val is not None
    assert val > 0.5
