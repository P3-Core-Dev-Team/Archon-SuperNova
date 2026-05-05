"""
test_imports.py — Smoke test that every public module imports without error.

Catches dead/broken imports immediately at test-collection time, before any
fixtures spin up.  This was the missing test that allowed the original
``inventory.run_phase_1`` / ``extraction.run_phase_2`` etc. AttributeError
defects to remain green in CI.
"""
from __future__ import annotations

import importlib

import pytest


_PUBLIC_MODULES = [
    "discovery",
    "discovery.cli",
    "discovery.orchestrator",
    "discovery.config",
    "discovery.exclusions",
    "discovery.extraction",
    "discovery.extraction_client",
    "discovery.fingerprint",
    "discovery.inventory",
    "discovery.logging_setup",
    "discovery.models",
    "discovery.pii_scan",
    "discovery.candidates",
    "discovery.report",
    "discovery.results_db",
    "discovery.run_log",
    "discovery.type_class",
    "discovery.validate",
]


@pytest.mark.parametrize("module_name", _PUBLIC_MODULES)
def test_module_imports(module_name: str) -> None:
    """Every public module must import cleanly."""
    importlib.import_module(module_name)


def test_no_runner_modules_left() -> None:
    """The ``*_runner.py`` modules were collapsed; importing them must fail."""
    for ghost in (
        "discovery.fingerprint_runner",
        "discovery.pii_runner",
        "discovery.candidate_runner",
        "discovery.validate_runner",
    ):
        with pytest.raises(ImportError):
            importlib.import_module(ghost)


def test_phase_functions_callable() -> None:
    """Every orchestrator entry point must be a module-level callable."""
    from discovery import (
        candidates,
        extraction,
        fingerprint,
        inventory,
        pii_scan,
        report,
        validate,
    )

    assert callable(inventory.run_phase_1)
    assert callable(extraction.run_phase_2)
    assert callable(fingerprint.run_phase_3a)
    assert callable(pii_scan.run_phase_3b)
    assert callable(candidates.run_phase_4)
    assert callable(validate.run_phase_5)
    assert callable(report.generate_all)
