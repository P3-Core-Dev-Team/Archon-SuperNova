"""Tests for the ``discovery.fallbacks`` helpers.

Covers:
  * ``optional_import`` — returns module on success, None on missing,
    logs once per missing module across multiple call sites.
  * ``safe_phase`` — runs the function on success, swallows exceptions
    + invokes fallback when ``enable_fallbacks=True``, re-raises when
    ``False``, logs ``phase_degraded`` correctly, handles a fallback
    that itself raises.
  * ``chunked`` — slices into contiguous batches; edge cases for empty
    input, ``size <= 0``, and a sequence shorter than the chunk size.
"""

from __future__ import annotations

import pytest

from discovery import fallbacks


# --------------------------------------------------------------------- #
# optional_import
# --------------------------------------------------------------------- #


def test_optional_import_success():
    """Existing module should be returned, not None."""
    mod = fallbacks.optional_import("json")
    assert mod is not None
    assert hasattr(mod, "loads")


def test_optional_import_missing_returns_none():
    """A nonexistent module returns None instead of raising."""
    # Reset the global "seen" set so the warn fires for this test.
    fallbacks._seen_missing.discard("definitely_not_a_real_module_xyz123")
    assert fallbacks.optional_import(
        "definitely_not_a_real_module_xyz123",
        hint="install with: pip install xyz",
    ) is None


def test_optional_import_logs_once_per_module(monkeypatch):
    """The warn log fires exactly once per missing module per process."""
    fallbacks._seen_missing.clear()
    calls: list[dict] = []

    class _StubLog:
        def warning(self, event, **kwargs):
            calls.append({"event": event, **kwargs})

    monkeypatch.setattr(fallbacks, "log", _StubLog())
    fallbacks.optional_import("not_a_real_module_abc")
    fallbacks.optional_import("not_a_real_module_abc")
    fallbacks.optional_import("not_a_real_module_abc")
    assert len(calls) == 1
    assert calls[0]["event"] == "optional_dependency_missing"
    assert calls[0]["module"] == "not_a_real_module_abc"


# --------------------------------------------------------------------- #
# safe_phase
# --------------------------------------------------------------------- #


def test_safe_phase_runs_fn_on_success():
    """Happy path: fn's return value flows through unchanged."""
    out = fallbacks.safe_phase("test_phase", lambda x: x * 2, 21)
    assert out == 42


def test_safe_phase_swallows_exception_when_enabled(monkeypatch):
    """With enable_fallbacks=True, exceptions log + return None."""
    calls: list[dict] = []

    class _StubLog:
        def warning(self, event, **kwargs):
            calls.append({"event": event, **kwargs})

        def error(self, event, **kwargs):
            calls.append({"event": event, **kwargs})

    monkeypatch.setattr(fallbacks, "log", _StubLog())

    def _boom():
        raise RuntimeError("kaboom")

    out = fallbacks.safe_phase(
        "test_phase", _boom, enable_fallbacks=True,
    )
    assert out is None
    assert any(c["event"] == "phase_degraded" for c in calls)
    degraded = next(c for c in calls if c["event"] == "phase_degraded")
    assert degraded["phase"] == "test_phase"
    assert degraded["error_type"] == "RuntimeError"


def test_safe_phase_reraises_when_disabled():
    """With enable_fallbacks=False, the original exception bubbles up."""
    def _boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        fallbacks.safe_phase(
            "test_phase", _boom, enable_fallbacks=False,
        )


def test_safe_phase_invokes_fallback(monkeypatch):
    """A fallback callable runs when the primary fn fails."""
    monkeypatch.setattr(
        fallbacks, "log",
        type("L", (), {"warning": lambda *a, **k: None,
                       "error": lambda *a, **k: None})(),
    )

    def _primary():
        raise RuntimeError("primary failed")

    out = fallbacks.safe_phase(
        "test_phase",
        _primary,
        fallback=lambda: "fallback-result",
        enable_fallbacks=True,
    )
    assert out == "fallback-result"


def test_safe_phase_handles_fallback_that_also_fails(monkeypatch):
    """Fallback raising returns None and logs both errors."""
    calls: list[dict] = []
    monkeypatch.setattr(
        fallbacks, "log",
        type("L", (), {
            "warning": lambda self, e, **kw: calls.append({"e": e, **kw}),
            "error":   lambda self, e, **kw: calls.append({"e": e, **kw}),
        })(),
    )

    def _primary():
        raise RuntimeError("primary")

    def _fallback():
        raise RuntimeError("fallback also broken")

    out = fallbacks.safe_phase(
        "test_phase",
        _primary,
        fallback=_fallback,
        enable_fallbacks=True,
    )
    assert out is None
    events = [c["e"] for c in calls]
    assert "phase_degraded" in events
    assert "phase_fallback_also_failed" in events


# --------------------------------------------------------------------- #
# chunked
# --------------------------------------------------------------------- #


def test_chunked_basic_split():
    out = fallbacks.chunked([1, 2, 3, 4, 5, 6, 7], 3)
    assert out == [[1, 2, 3], [4, 5, 6], [7]]


def test_chunked_empty_input():
    assert fallbacks.chunked([], 5) == []


def test_chunked_size_zero_returns_one_batch():
    """size <= 0 disables batching — entire seq in one chunk."""
    assert fallbacks.chunked([1, 2, 3], 0) == [[1, 2, 3]]


def test_chunked_size_negative_returns_one_batch():
    assert fallbacks.chunked([1, 2, 3], -7) == [[1, 2, 3]]


def test_chunked_seq_shorter_than_size():
    """Single chunk shorter than ``size`` is fine."""
    assert fallbacks.chunked([1, 2], 10) == [[1, 2]]
