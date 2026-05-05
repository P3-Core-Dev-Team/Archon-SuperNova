"""
Optional semantic name-similarity for FK candidate generation.

Loads sentence-transformers if available; gracefully no-ops with a flag if
not. Uses lru_cache to embed each unique column name only once per process.

Default model: all-MiniLM-L6-v2 (~80 MB, CPU-fast, single-vec 384-dim).

Public API
----------
SEMANTIC_AVAILABLE : bool
    True iff sentence-transformers + the model loaded successfully.

best_similarity(a, b) -> float
    max(lex, semantic) when semantic is available, else just lex. In [0, 1].

semantic_similarity(a, b) -> Optional[float]
    Cosine similarity clamped to [0, 1], or None if SEMANTIC_AVAILABLE is False.

lex_similarity(a, b) -> float
    Lexical similarity (delegates to discovery.scoring.name_similarity if
    that helper exists, otherwise falls back to a local difflib + plural-norm
    implementation). Always available.

warmup() -> None
    Force the lazy model load. No-op if sentence-transformers isn't installed
    or the model fails to load.
"""
from __future__ import annotations

import difflib
import functools
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# True iff sentence-transformers is installed AND the default model loaded.
# Starts False; flips to True on the first successful _get_model() call.
SEMANTIC_AVAILABLE: bool = False

# Default sentence-transformers model. Small (~80 MB), CPU-friendly, 384-dim.
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Cached model instance. None until first successful load.
_MODEL: Optional[Any] = None

# Has the load attempt been made (success or failure)? Used to log the
# "missing/failed model" warning exactly once.
_LOAD_ATTEMPTED: bool = False


# ---------------------------------------------------------------------------
# Lexical similarity — delegates to discovery.scoring if possible, otherwise
# falls back to a local difflib + naive plural-strip.
# ---------------------------------------------------------------------------


def _local_plural_normalize(name: str) -> str:
    """Lowercase + strip simple trailing plural suffixes from each token.

    Used only when discovery.scoring.name_similarity is not yet available
    (A1/A3 add it later; this fallback keeps lex_similarity always working).
    """
    if not name:
        return ""
    parts = re.split(r"[_\s\.\-]+", name.lower())
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if len(p) > 3 and p.endswith("ies"):
            p = p[:-3] + "y"
        elif len(p) > 2 and p.endswith("es") and not p.endswith("ses"):
            p = p[:-2]
        elif len(p) > 1 and p.endswith("s") and not p.endswith("ss"):
            p = p[:-1]
        out.append(p)
    return "_".join(out)


def _local_lex_similarity(a: str, b: str) -> float:
    """Fallback lexical similarity using difflib + plural normalization."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(
        None, _local_plural_normalize(a), _local_plural_normalize(b)
    ).ratio()


# Resolve the scoring helper once at import time. If discovery.scoring
# eventually exports a `name_similarity(a, b, plural_normalize=True)` helper
# we use it; otherwise we fall back to the local difflib version above.
try:
    from discovery.scoring import name_similarity as _scoring_name_similarity  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover — defensive: scoring always importable
    _scoring_name_similarity = None  # type: ignore[assignment]


def lex_similarity(a: str, b: str) -> float:
    """Lexical similarity using scoring.name_similarity (with plural norm).

    Always returns a number in [0, 1]. Empty input on either side -> 0.0.
    """
    if not a or not b:
        return 0.0
    fn = _scoring_name_similarity
    if fn is not None:
        try:
            val = fn(a, b, plural_normalize=True)
            return max(0.0, min(1.0, float(val)))
        except TypeError:
            # scoring.name_similarity exists but doesn't accept the kwarg yet
            try:
                val = fn(a, b)
                return max(0.0, min(1.0, float(val)))
            except Exception:  # noqa: BLE001 — fall through to local
                pass
        except Exception:  # noqa: BLE001 — fall through to local
            pass
    return _local_lex_similarity(a, b)


# ---------------------------------------------------------------------------
# Semantic similarity (sentence-transformers, lazy-loaded)
# ---------------------------------------------------------------------------


def _get_model() -> Optional[Any]:
    """Lazy-load the sentence-transformers model.

    Returns the model instance, or None if loading failed (in which case
    SEMANTIC_AVAILABLE stays False and a warning is logged exactly once).
    """
    global _MODEL, SEMANTIC_AVAILABLE, _LOAD_ATTEMPTED

    if _MODEL is not None:
        return _MODEL
    if _LOAD_ATTEMPTED and not SEMANTIC_AVAILABLE:
        # Already failed once — don't retry, don't re-log.
        return None

    _LOAD_ATTEMPTED = True
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    except ImportError:
        logger.warning(
            "sentence-transformers not installed; semantic name similarity "
            "disabled. Install with: pip install 'discovery[semantic]'"
        )
        SEMANTIC_AVAILABLE = False
        return None

    try:
        _MODEL = SentenceTransformer(_MODEL_NAME, device="cpu")
        SEMANTIC_AVAILABLE = True
        return _MODEL
    except Exception as exc:  # noqa: BLE001 — network / disk / corrupt model
        logger.warning(
            "Failed to load sentence-transformers model %s: %s. "
            "Semantic name similarity disabled.",
            _MODEL_NAME,
            exc,
        )
        SEMANTIC_AVAILABLE = False
        _MODEL = None
        return None


@functools.lru_cache(maxsize=20000)
def _embed(name: str):  # type: ignore[no-untyped-def]
    """Encode a single column name to a numpy vector. Cached per-process.

    Returns the encoded vector, or None if the model isn't loaded.
    """
    model = _get_model()
    if model is None:
        return None
    try:
        vec = model.encode(name, convert_to_numpy=True, show_progress_bar=False)
        return vec
    except Exception as exc:  # noqa: BLE001 — encoding shouldn't crash callers
        logger.debug("encode() failed for %r: %s", name, exc)
        return None


def _canon_pair(a: str, b: str) -> tuple[str, str]:
    """Canonicalise an unordered (a, b) pair so the lru_cache hits regardless
    of argument order (cosine similarity is symmetric)."""
    return (a, b) if a <= b else (b, a)


@functools.lru_cache(maxsize=20000)
def _semantic_similarity_cached(a: str, b: str) -> Optional[float]:
    """Cached cosine similarity, clamped to [0, 1]. Reads SEMANTIC_AVAILABLE
    dynamically so monkeypatching it in tests works."""
    # NB: read the module attribute at call time so test monkeypatches win.
    import sys as _sys

    mod = _sys.modules[__name__]
    if not getattr(mod, "SEMANTIC_AVAILABLE", False):
        # Try to load on demand — if it succeeds, SEMANTIC_AVAILABLE flips.
        if _get_model() is None:
            return None
        if not getattr(mod, "SEMANTIC_AVAILABLE", False):
            return None

    va = _embed(a)
    vb = _embed(b)
    if va is None or vb is None:
        return None

    try:
        import numpy as np  # noqa: PLC0415
    except ImportError:  # pragma: no cover — numpy is a hard dep transitively
        return None

    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    cos = float(np.dot(va, vb)) / (na * nb)
    # Clamp to [0, 1] for our use (cosine can be in [-1, 1]).
    return max(0.0, min(1.0, cos))


def semantic_similarity(a: str, b: str) -> Optional[float]:
    """Cosine similarity in [0, 1] (clamped from [-1, 1]), or None if
    sentence-transformers / the model isn't available.

    Cached per (a, b) pair (order-canonicalised since cosine is symmetric).
    """
    if not a or not b:
        # Per spec: empty strings -> 0.0 for similarity, but only return a
        # number if semantic is "available" — otherwise None for consistency.
        if not SEMANTIC_AVAILABLE:
            return None
        return 0.0
    if not SEMANTIC_AVAILABLE:
        # Try one lazy load; if it still isn't available, give up.
        if _get_model() is None:
            return None
    pa, pb = _canon_pair(a, b)
    return _semantic_similarity_cached(pa, pb)


# ---------------------------------------------------------------------------
# Combined "best of both worlds" helper
# ---------------------------------------------------------------------------


def best_similarity(a: str, b: str) -> float:
    """Return max(lex_similarity, semantic_similarity) when semantic is
    available, otherwise just lex_similarity. Always a number in [0, 1]."""
    lex = lex_similarity(a, b)
    sem = semantic_similarity(a, b)
    if sem is None:
        return lex
    return max(lex, sem)


# ---------------------------------------------------------------------------
# Warmup — useful at process startup so the first call doesn't pay load cost
# ---------------------------------------------------------------------------


def warmup() -> None:
    """Force model load. Useful at process startup so the first call doesn't
    pay the load cost. No-op (with a single warning) if not available."""
    _get_model()
