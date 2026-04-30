"""
Stage-level fallbacks for the discovery pipeline.

The pipeline has several optional dependencies (Hyperscan, sentence-
transformers, spaCy NER, stdnum validators, the Java extraction service)
and a number of phases that can be tolerably skipped on a degraded path
without invalidating the run.  Today those fallbacks are duplicated as
ad-hoc try/except blocks scattered across pii_scan.py, candidates.py,
clustering.py, and orchestrator.py.

This module provides two thin canonical helpers so callers stop
reinventing the pattern:

* :func:`optional_import` — wrapper for ``try: import X except ImportError``
  that returns ``None`` instead of raising.  Logs a single WARN with a
  stable structured field set so operators can grep for missing deps.

* :func:`safe_phase` — wraps a phase function so that an exception
  during the phase is logged and (optionally) replaced with a fallback
  callable instead of bubbling up and aborting the whole run.

Neither helper is required — the existing in-line try/except patterns
remain valid.  These exist to deduplicate and standardise.

Operator config: ``OrchestrationConfig.enable_phase_fallbacks`` (default
True) toggles ``safe_phase`` between "log and degrade" and "log and
re-raise".  Setting False is the right thing for CI / debugging.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Optional, TypeVar

import structlog

log = structlog.get_logger()

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Optional-import helper
# ---------------------------------------------------------------------------


_seen_missing: set[str] = set()
"""Modules we've already logged as missing — keeps the warn log to one
line per process per missing dep instead of one per call site."""


def optional_import(module_name: str, *, hint: Optional[str] = None) -> Optional[Any]:
    """Try to ``import module_name``; on ImportError return ``None``.

    The first call per process logs a WARN with the module name and an
    install hint; subsequent calls for the same module are silent.

    Parameters
    ----------
    module_name:
        Dotted module path, e.g. ``"hyperscan"`` or ``"stdnum.iban"``.
    hint:
        Optional install hint shown in the warn log, e.g.
        ``"pip install discovery[semantic]"``.  Plain prose is fine.
    """
    try:
        return importlib.import_module(module_name)
    except ImportError:
        if module_name not in _seen_missing:
            _seen_missing.add(module_name)
            log.warning(
                "optional_dependency_missing",
                module=module_name,
                hint=hint or "(no hint provided)",
            )
        return None


# ---------------------------------------------------------------------------
# Phase-level safe wrapper
# ---------------------------------------------------------------------------


def safe_phase(
    phase_name: str,
    fn: Callable[..., T],
    *args: Any,
    fallback: Optional[Callable[..., T]] = None,
    enable_fallbacks: bool = True,
    **kwargs: Any,
) -> Optional[T]:
    """Run ``fn(*args, **kwargs)`` inside a try/except.

    On exception:
      * If ``enable_fallbacks`` is False, re-raise (fail-fast mode).
      * Else log the exception under ``phase_degraded`` at WARN, run the
        ``fallback`` (if supplied) and return its result, or return None.

    The intent is to replace blocks like::

        try:
            from discovery import composite_fk
            _run_phase(...)
        except Exception as exc:
            log.warning("composite_fk_phase_skipped", error=str(exc))

    with::

        safe_phase("composite_fk", _run_phase, ...,
                   enable_fallbacks=cfg.enable_phase_fallbacks)

    so the log-format and degrade-vs-raise decision live in one place.
    Existing call sites that want their own custom error-message field
    set are free to keep their bespoke try/except — this is a tool, not
    a mandate.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        if not enable_fallbacks:
            raise
        log.warning(
            "phase_degraded",
            phase=phase_name,
            error=str(exc),
            error_type=type(exc).__name__,
            has_fallback=fallback is not None,
        )
        if fallback is not None:
            try:
                return fallback(*args, **kwargs)
            except Exception as fallback_exc:
                log.error(
                    "phase_fallback_also_failed",
                    phase=phase_name,
                    error=str(fallback_exc),
                    error_type=type(fallback_exc).__name__,
                    exc_info=True,
                )
        return None


# ---------------------------------------------------------------------------
# Adaptive batching helper
# ---------------------------------------------------------------------------


def chunked(seq: list[T], size: int) -> list[list[T]]:
    """Split ``seq`` into contiguous chunks of at most ``size`` items.

    Returns an empty list when ``seq`` is empty.  When ``size <= 0`` the
    chunk size falls back to the full sequence — equivalent to the
    pre-batching one-shot behaviour.

    Used by the worker-pool batching wrappers in pii_scan / validate so
    Pool.map() submits in slices instead of one huge list.
    """
    if size <= 0 or not seq:
        return [seq] if seq else []
    return [seq[i : i + size] for i in range(0, len(seq), size)]
