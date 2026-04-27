"""
pii_ner.py — Optional spaCy-backed named-entity recognition.

The NER pass is opt-in (``config.pii.detectors.spacy_ner: bool = False``)
because it requires the ``spacy`` library plus the ``en_core_web_sm`` model
(~13 MB, separate download).  Without those, ``AVAILABLE`` is ``False`` and
:func:`scan_text` returns an empty list, leaving the regex matcher unaffected.

When enabled, the module recognises ``PERSON`` / ``GPE`` / ``ORG`` / ``LOC``
/ ``DATE`` entities — categories that regex cannot reasonably target.  The
calling code converts each entity to a PII finding with type
``NAME``/``LOCATION``/``ORG``/``DATE_NER`` etc.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy spaCy import
# ---------------------------------------------------------------------------

_NLP = None
AVAILABLE = False
_LOAD_ERR: str | None = None

try:
    import spacy  # type: ignore[import]

    try:
        _NLP = spacy.load("en_core_web_sm")
        AVAILABLE = True
    except OSError as exc:  # model not downloaded
        _LOAD_ERR = (
            f"spaCy installed but model 'en_core_web_sm' missing: {exc}. "
            "Run `python -m spacy download en_core_web_sm` to enable NER."
        )
except ImportError as exc:
    _LOAD_ERR = f"spaCy not installed: {exc}. NER disabled."


_NER_LABEL_TO_PII_TYPE: dict[str, str] = {
    "PERSON": "NAME",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "ORG": "ORG",
    "DATE": "DATE_NER",
}


@dataclass(frozen=True)
class NerEntity:
    """A single NER hit returned by :func:`scan_text`."""

    label: str          # spaCy label (PERSON, GPE, ORG, LOC, DATE)
    pii_type: str       # mapped pii_type emitted to findings
    text: str           # surface form
    start_char: int
    end_char: int


def scan_text(text: str) -> list[NerEntity]:
    """Run NER on *text* and return entities mapped to PII types.

    If spaCy / the model is unavailable, returns ``[]``.
    """
    if not AVAILABLE or _NLP is None or not text:
        return []
    try:
        doc = _NLP(text)
    except Exception as exc:
        log.warning("NER scan failed: %s", exc)
        return []
    out: list[NerEntity] = []
    for ent in doc.ents:
        pii_type = _NER_LABEL_TO_PII_TYPE.get(ent.label_)
        if pii_type is None:
            continue
        out.append(
            NerEntity(
                label=ent.label_,
                pii_type=pii_type,
                text=ent.text,
                start_char=ent.start_char,
                end_char=ent.end_char,
            )
        )
    return out


def availability_message() -> str:
    """Single-line banner suitable for logging at scan-start."""
    if AVAILABLE:
        return "pii_ner: spaCy en_core_web_sm loaded"
    return f"pii_ner: disabled — {_LOAD_ERR or 'unknown reason'}"
