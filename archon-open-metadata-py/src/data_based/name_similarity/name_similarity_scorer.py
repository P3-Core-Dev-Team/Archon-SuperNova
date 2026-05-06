import difflib
import functools
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_MODEL: Optional[Any] = None
_LOAD_ATTEMPTED = False
_AVAILABLE = False


def _plural_normalize(name: str) -> str:
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


def _lex(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(
        None, _plural_normalize(a), _plural_normalize(b)
    ).ratio()


def _get_model() -> Optional[Any]:
    global _MODEL, _LOAD_ATTEMPTED, _AVAILABLE
    if _MODEL is not None:
        return _MODEL
    if _LOAD_ATTEMPTED and not _AVAILABLE:
        return None
    _LOAD_ATTEMPTED = True
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.warning("sentence-transformers not installed; semantic similarity disabled.")
        _AVAILABLE = False
        return None
    try:
        _MODEL = SentenceTransformer(_MODEL_NAME, device="cpu")
        _AVAILABLE = True
        return _MODEL
    except Exception as exc:
        logger.warning("Failed to load %s: %s", _MODEL_NAME, exc)
        _AVAILABLE = False
        return None


@functools.lru_cache(maxsize=20000)
def _embed(name: str):
    model = _get_model()
    if model is None:
        return None
    try:
        return model.encode(name, convert_to_numpy=True, show_progress_bar=False)
    except Exception:
        return None


@functools.lru_cache(maxsize=20000)
def _semantic(a: str, b: str) -> Optional[float]:
    if _get_model() is None:
        return None
    va = _embed(a)
    vb = _embed(b)
    if va is None or vb is None:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    cos = float(np.dot(va, vb)) / (na * nb)
    return max(0.0, min(1.0, cos))


class NameSimilarityScorer:
    """
    Stage 10: Hybrid lexical + semantic name similarity for FK candidate
    matching.  Lexical = difflib SequenceMatcher with plural-aware
    normalisation (always available).  Semantic = sentence-transformers
    cosine on all-MiniLM-L6-v2 (lazy-loaded; falls back to lex-only when
    the model can't be loaded).
    """

    @staticmethod
    def lex_similarity(a: str, b: str) -> float:
        return _lex(a, b)

    @staticmethod
    def semantic_similarity(a: str, b: str) -> Optional[float]:
        if not a or not b:
            return None if not _AVAILABLE else 0.0
        pa, pb = (a, b) if a <= b else (b, a)
        return _semantic(pa, pb)

    @staticmethod
    def best_similarity(a: str, b: str) -> float:
        lex = _lex(a, b)
        sem = NameSimilarityScorer.semantic_similarity(a, b)
        return lex if sem is None else max(lex, sem)

    @staticmethod
    def score_pairs(pairs: list[dict]) -> list[dict]:
        """Bulk: each input row is ``{a, b}``; output adds ``lex``,
        ``semantic`` (nullable), and ``best``."""
        out: list[dict] = []
        for p in pairs:
            a = str(p.get("a", ""))
            b = str(p.get("b", ""))
            sem = NameSimilarityScorer.semantic_similarity(a, b)
            lex = _lex(a, b)
            out.append({
                "a": a, "b": b,
                "lex": round(lex, 4),
                "semantic": None if sem is None else round(sem, 4),
                "best": round(lex if sem is None else max(lex, sem), 4),
            })
        return out

    @staticmethod
    def warmup() -> bool:
        """Force model load.  Returns True if the model loaded
        successfully (semantic available)."""
        _get_model()
        return _AVAILABLE
