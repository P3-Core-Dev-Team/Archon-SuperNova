"""
test_orchestrator.py — Unit tests for the run_all orchestrator.

Strategy
--------
We import the real phase modules (not MagicMock-injected `sys.modules`) and
use ``unittest.mock.patch.object`` to intercept the ``run_phase_N`` callables.
This guarantees the test will fail at collection time if any of the expected
module-level functions disappear (catches the original C1–C4 integration
defects).

The orchestrator's other concerns (RunLog instance, ExtractionClient,
get_engine) are mocked at the import sites.

Tests assert:
1. Every phase module exposes the expected ``run_phase_N`` function with the
   expected signature.
2. Phases run in order when nothing is complete.
3. Already-complete phases are skipped.
4. ``skip_phases`` list prevents specific phases from running.
5. Exceptions propagate and trigger run_log.fail.
6. ``limit`` is forwarded to Phase 2 and Phase 5 only.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from discovery import (
    candidates,
    extraction,
    fingerprint,
    inventory,
    pii_scan,
    report,
    validate,
)


# ---------------------------------------------------------------------------
# Static integration: every phase module must expose run_phase_N
# ---------------------------------------------------------------------------


class TestPhaseFunctionsExist:
    """The orchestrator + CLI rely on these specific module-level functions."""

    def test_run_phase_1_exists(self):
        assert callable(getattr(inventory, "run_phase_1"))

    def test_run_phase_2_exists(self):
        assert callable(getattr(extraction, "run_phase_2"))

    def test_run_phase_3a_exists(self):
        assert callable(getattr(fingerprint, "run_phase_3a"))

    def test_run_phase_3b_exists(self):
        assert callable(getattr(pii_scan, "run_phase_3b"))

    def test_run_phase_4_exists(self):
        assert callable(getattr(candidates, "run_phase_4"))

    def test_run_phase_5_exists(self):
        assert callable(getattr(validate, "run_phase_5"))

    def test_report_generate_all_exists(self):
        assert callable(getattr(report, "generate_all"))


class TestPhaseSignatures:
    """Verify each orchestrator-facing function has a compatible signature."""

    def test_run_phase_1_signature(self):
        sig = inspect.signature(inventory.run_phase_1)
        # Must accept (engine, extraction_client, config)
        params = list(sig.parameters)
        assert len(params) == 3, params

    def test_run_phase_2_signature(self):
        sig = inspect.signature(extraction.run_phase_2)
        # Must accept (engine, extraction_client, config) and a `limit` kwarg.
        params = sig.parameters
        assert "engine" in params
        assert "extraction_client" in params
        assert "config" in params
        assert "limit" in params

    def test_run_phase_3a_signature(self):
        sig = inspect.signature(fingerprint.run_phase_3a)
        assert "engine" in sig.parameters
        assert "config" in sig.parameters

    def test_run_phase_3b_signature(self):
        sig = inspect.signature(pii_scan.run_phase_3b)
        assert "engine" in sig.parameters
        assert "config" in sig.parameters

    def test_run_phase_4_signature(self):
        sig = inspect.signature(candidates.run_phase_4)
        # Phase 4 takes only (engine, config) — no parquet_dir.
        params = list(sig.parameters)
        assert params == ["engine", "config"], params

    def test_run_phase_5_signature(self):
        sig = inspect.signature(validate.run_phase_5)
        # Phase 5 must accept a `limit` kwarg.
        params = sig.parameters
        assert "engine" in params
        assert "config" in params
        assert "limit" in params


# ---------------------------------------------------------------------------
# Phase modules must not write their own global run_log lifecycle.
# ---------------------------------------------------------------------------


class TestPhaseFunctionsDoNotOwnGlobalRunLog:
    """
    The orchestrator wraps each phase with run_log start/succeed/fail at
    scope_type='global'.  The phase functions themselves must not also write
    that row (idempotent, but it clobbers the orchestrator's timestamps on
    re-run).  This test parses the source to ensure the inner writes stay
    deleted.
    """

    @staticmethod
    def _global_writes(source: str) -> list[str]:
        """Return any line that calls run_log.{start,succeed,fail}(...,'global'...)."""
        offending: list[str] = []
        for line in source.splitlines():
            stripped = line.strip()
            if "'global'" not in stripped and '"global"' not in stripped:
                continue
            if any(
                op in stripped
                for op in ("run_log.start(", "run_log.succeed(", "run_log.fail(")
            ):
                offending.append(stripped)
        return offending

    def test_inventory_does_not_write_global_run_log(self):
        import inspect as _inspect

        src = _inspect.getsource(inventory)
        offenders = self._global_writes(src)
        assert offenders == [], (
            "inventory.py writes 'global' run_log rows directly — those rows "
            "must be owned by the orchestrator wrapper. Found:\n  "
            + "\n  ".join(offenders)
        )

    def test_extraction_does_not_write_global_run_log(self):
        import inspect as _inspect

        src = _inspect.getsource(extraction)
        offenders = self._global_writes(src)
        assert offenders == [], (
            "extraction.py writes 'global' run_log rows directly — those rows "
            "must be owned by the orchestrator wrapper. Found:\n  "
            + "\n  ".join(offenders)
        )


# ---------------------------------------------------------------------------
# Behaviour tests: orchestrator drives the phases correctly
# ---------------------------------------------------------------------------


class _FakeResultsDb:
    user = "user"
    password = "pass"
    host = "localhost"
    port = 5432
    database = "discovery_results"

    @property
    def dsn(self) -> str:
        return "postgresql+psycopg2://u:p@h:5432/d"


class _FakeExtractionService:
    base_url = "http://localhost:8080"
    auth_token = "token"
    request_timeout_seconds = 3600


class _FakeStorage:
    base_path = "/data/parquet"


class _FakeReporting:
    output_dir = "/data/reports"


class _FakeWorkers:
    extract = 8
    fingerprint = 16
    pii_scan = 16
    validate = 8


class _FakeOrchestration:
    workers = _FakeWorkers()


class FakeConfig:
    results_db = _FakeResultsDb()
    extraction_service = _FakeExtractionService()
    storage = _FakeStorage()
    reporting = _FakeReporting()
    orchestration = _FakeOrchestration()


def _patch_orchestrator(complete: set[str] | None = None):
    """
    Build the patch context manager set used by the behaviour tests.

    Returns a list of (started) patch objects PLUS a dict of mocks the test
    can introspect.  Caller is responsible for stopping the patches.
    """
    if complete is None:
        complete = set()

    run_log_instance = MagicMock()
    run_log_instance.is_complete.side_effect = (
        lambda phase, scope_type, scope_id: phase in complete
    )

    fake_engine = MagicMock()
    fake_client = MagicMock()

    patches = [
        # Stub run_log.RunLog so the orchestrator's `RunLog(engine)` call yields
        # our mock instance.
        patch("discovery.run_log.RunLog", return_value=run_log_instance),
        # Stub get_engine so we don't try to connect to Postgres.
        patch("discovery.results_db.get_engine", return_value=fake_engine),
        # Stub ExtractionClient so we don't open a real httpx Client.
        patch(
            "discovery.extraction_client.ExtractionClient",
            return_value=fake_client,
        ),
        # Intercept each phase function on the *real* module.  If any of these
        # attributes is missing, the patch decorator will raise AttributeError
        # and the test will fail loud.
        patch.object(inventory, "run_phase_1"),
        patch.object(extraction, "run_phase_2"),
        patch.object(fingerprint, "run_phase_3a"),
        patch.object(pii_scan, "run_phase_3b"),
        patch.object(candidates, "run_phase_4"),
        patch.object(validate, "run_phase_5"),
        patch.object(report, "generate_all"),
    ]
    started = [p.start() for p in patches]
    mocks = {
        "run_log_instance": run_log_instance,
        "engine": fake_engine,
        "client": fake_client,
        "run_phase_1": started[3],
        "run_phase_2": started[4],
        "run_phase_3a": started[5],
        "run_phase_3b": started[6],
        "run_phase_4": started[7],
        "run_phase_5": started[8],
        "generate_all": started[9],
    }
    return patches, mocks


def _stop_all(patches):
    for p in patches:
        p.stop()


class TestRunAllPhaseOrder:
    def test_all_phases_called(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        try:
            run_all(FakeConfig())
        finally:
            _stop_all(patches)

        mocks["run_phase_1"].assert_called_once()
        mocks["run_phase_2"].assert_called_once()
        mocks["run_phase_3a"].assert_called_once()
        mocks["run_phase_3b"].assert_called_once()
        mocks["run_phase_4"].assert_called_once()
        mocks["run_phase_5"].assert_called_once()
        mocks["generate_all"].assert_called_once()

    def test_run_log_succeed_called_for_each_phase(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        try:
            run_all(FakeConfig())
        finally:
            _stop_all(patches)

        # 14 phases total: 7 base + composite_fk, polymorphic_fk, jsonb_fk,
        # inheritance, pii_propagation, pii_leak (advanced FK detectors run after
        # validate) + clustering (after pii_leak, before report) + report.
        assert mocks["run_log_instance"].succeed.call_count == 14

    def test_run_log_global_lifecycle_owned_by_orchestrator(self):
        """
        Each phase produces exactly one (phase, 'global', None) lifecycle write
        — start + succeed.  Inner phase functions must NOT write the global
        lifecycle themselves; otherwise a re-run clobbers the orchestrator's
        timestamps.
        """
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        try:
            run_all(FakeConfig())
        finally:
            _stop_all(patches)

        run_log = mocks["run_log_instance"]
        # Exactly one start() and one succeed() per phase — 14 phases total
        # (inventory, extract, fingerprint, pii_scan, candidate_gen, validate,
        # composite_fk, polymorphic_fk, jsonb_fk, inheritance, pii_propagation,
        # pii_leak, clustering, report).
        # If an inner phase function also wrote "global", these counts would
        # be 15+ per type.
        assert run_log.start.call_count == 14
        assert run_log.succeed.call_count == 14
        # Phases that completed cleanly must not also be marked failed.
        assert run_log.fail.call_count == 0

        # Every start/succeed call must target ('phase', 'global', None).
        for call in list(run_log.start.call_args_list) + list(
            run_log.succeed.call_args_list
        ):
            args = call.args
            assert args[1] == "global", call
            assert args[2] is None, call

    def test_limit_forwarded_to_phase2_and_phase5(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        try:
            run_all(FakeConfig(), limit=42)
        finally:
            _stop_all(patches)

        # Phase 2 — limit is passed as kwarg.
        phase2_call = mocks["run_phase_2"].call_args
        assert phase2_call.kwargs.get("limit") == 42, phase2_call

        # Phase 5 — limit kwarg.
        phase5_call = mocks["run_phase_5"].call_args
        assert phase5_call.kwargs.get("limit") == 42, phase5_call

        # Phase 3a should not have a limit kwarg (or it should be absent).
        phase3a_call = mocks["run_phase_3a"].call_args
        assert "limit" not in phase3a_call.kwargs

    def test_limit_none_by_default(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        try:
            run_all(FakeConfig())
        finally:
            _stop_all(patches)

        phase2_call = mocks["run_phase_2"].call_args
        assert phase2_call.kwargs.get("limit") is None


class TestRunAllSkipping:
    def test_complete_phase_is_skipped(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator(complete={"inventory"})
        try:
            run_all(FakeConfig())
        finally:
            _stop_all(patches)

        mocks["run_phase_1"].assert_not_called()
        mocks["run_phase_2"].assert_called_once()

    def test_skip_phases_list(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        try:
            run_all(FakeConfig(), skip_phases=["fingerprint", "pii_scan"])
        finally:
            _stop_all(patches)

        mocks["run_phase_3a"].assert_not_called()
        mocks["run_phase_3b"].assert_not_called()
        mocks["run_phase_1"].assert_called_once()
        mocks["run_phase_2"].assert_called_once()

    def test_all_phases_complete_nothing_runs(self):
        from discovery.orchestrator import run_all

        all_phases = {
            "inventory", "extract", "fingerprint", "pii_scan",
            "candidate_gen", "validate", "report",
        }
        patches, mocks = _patch_orchestrator(complete=all_phases)
        try:
            run_all(FakeConfig())
        finally:
            _stop_all(patches)

        for key in (
            "run_phase_1", "run_phase_2", "run_phase_3a", "run_phase_3b",
            "run_phase_4", "run_phase_5", "generate_all",
        ):
            mocks[key].assert_not_called()

    def test_skip_phases_none_defaults_to_empty(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        try:
            run_all(FakeConfig(), skip_phases=None)
        finally:
            _stop_all(patches)

        mocks["run_phase_1"].assert_called_once()


class TestRunAllErrorHandling:
    def test_phase_exception_propagates(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        mocks["run_phase_2"].side_effect = RuntimeError("extraction service down")
        try:
            with pytest.raises(RuntimeError, match="extraction service down"):
                run_all(FakeConfig())
        finally:
            _stop_all(patches)

    def test_run_log_fail_called_on_exception(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        mocks["run_phase_2"].side_effect = RuntimeError("boom")
        try:
            with pytest.raises(RuntimeError):
                run_all(FakeConfig())
        finally:
            _stop_all(patches)

        fail_calls = mocks["run_log_instance"].fail.call_args_list
        failed_phases = [
            c.args[0] if c.args else c.kwargs.get("phase") for c in fail_calls
        ]
        assert "extract" in failed_phases

    def test_phases_after_failure_are_not_called(self):
        from discovery.orchestrator import run_all

        patches, mocks = _patch_orchestrator()
        mocks["run_phase_2"].side_effect = RuntimeError("network error")
        try:
            with pytest.raises(RuntimeError):
                run_all(FakeConfig())
        finally:
            _stop_all(patches)

        for key in (
            "run_phase_3a", "run_phase_3b",
            "run_phase_4", "run_phase_5", "generate_all",
        ):
            mocks[key].assert_not_called()


# ---------------------------------------------------------------------------
# Two-pass orchestration (run_all_two_pass) — C2 minimal-scan
# ---------------------------------------------------------------------------


def _patch_two_pass_orchestrator(
    complete: set[str] | None = None,
    survivors: list[int] | None = None,
):
    """
    Patch helper for run_all_two_pass tests.

    In addition to the same phase-function patches used by ``_patch_orchestrator``,
    we also intercept ``_surviving_candidate_table_ids`` (so the touched-table
    set is deterministic), ``_reset_fingerprint_state`` (so it doesn't try to
    issue UPDATE SQL through a MagicMock engine), and the ``cleanup`` import
    used by ``_enforce_disk_cap_safely``.
    """
    if complete is None:
        complete = set()
    if survivors is None:
        survivors = []

    run_log_instance = MagicMock()
    run_log_instance.is_complete.side_effect = (
        lambda phase, scope_type, scope_id: phase in complete
    )

    fake_engine = MagicMock()
    fake_client = MagicMock()

    patches = [
        patch("discovery.run_log.RunLog", return_value=run_log_instance),
        patch("discovery.results_db.get_engine", return_value=fake_engine),
        patch(
            "discovery.extraction_client.ExtractionClient",
            return_value=fake_client,
        ),
        patch.object(inventory, "run_phase_1"),
        patch.object(extraction, "run_phase_2"),
        patch.object(fingerprint, "run_phase_3a"),
        patch.object(pii_scan, "run_phase_3b"),
        patch.object(candidates, "run_phase_4"),
        patch.object(validate, "run_phase_5"),
        patch.object(report, "generate_all"),
        # Helpers internal to orchestrator.py — patch by name on the module.
        patch(
            "discovery.orchestrator._surviving_candidate_table_ids",
            return_value=list(survivors),
        ),
        patch(
            "discovery.orchestrator._reset_fingerprint_state",
            return_value=len(survivors),
        ),
        patch(
            "discovery.orchestrator._enforce_disk_cap_safely",
            return_value=None,
        ),
    ]
    started = [p.start() for p in patches]

    mocks = {
        "run_log_instance": run_log_instance,
        "engine": fake_engine,
        "client": fake_client,
        "run_phase_1": started[3],
        "run_phase_2": started[4],
        "run_phase_3a": started[5],
        "run_phase_3b": started[6],
        "run_phase_4": started[7],
        "run_phase_5": started[8],
        "generate_all": started[9],
        "surviving_table_ids": started[10],
        "reset_fingerprint_state": started[11],
        "enforce_disk_cap": started[12],
    }
    return patches, mocks


class TestRunAllTwoPass:
    """Verify the two-pass orchestrator drives phases in the correct order."""

    def test_phase_2_called_twice_with_distinct_modes(self):
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(survivors=[10, 20])
        try:
            run_all_two_pass(FakeConfig(), sample_pct=1.0)
        finally:
            _stop_all(patches)

        # Phase 2 called twice — mode='sample' then mode='full_subset'.
        assert mocks["run_phase_2"].call_count == 2
        first_call = mocks["run_phase_2"].call_args_list[0]
        second_call = mocks["run_phase_2"].call_args_list[1]

        assert first_call.kwargs.get("mode") == "sample"
        # sample_pct now uses percentage units (0, 100]; 1.0 == 1%.
        assert first_call.kwargs.get("sample_pct") == 1.0

        assert second_call.kwargs.get("mode") == "full_subset"
        assert second_call.kwargs.get("table_ids") == [10, 20]

    def test_default_sample_pct_forwarded_to_phase_2(self):
        """
        Caller omits ``sample_pct``; the orchestrator forwards its 1.0
        default verbatim to ``extraction.run_phase_2``.
        """
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(survivors=[1])
        try:
            run_all_two_pass(FakeConfig())
        finally:
            _stop_all(patches)

        first_call = mocks["run_phase_2"].call_args_list[0]
        assert first_call.kwargs.get("mode") == "sample"
        assert first_call.kwargs.get("sample_pct") == 1.0

    def test_custom_sample_pct_forwarded_to_phase_2(self):
        """A non-default sample_pct flows through to extraction.run_phase_2."""
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(survivors=[1])
        try:
            run_all_two_pass(FakeConfig(), sample_pct=5.0)
        finally:
            _stop_all(patches)

        first_call = mocks["run_phase_2"].call_args_list[0]
        assert first_call.kwargs.get("sample_pct") == 5.0

    def test_phase_3a_called_twice_when_survivors_exist(self):
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(survivors=[5])
        try:
            run_all_two_pass(FakeConfig())
        finally:
            _stop_all(patches)

        # First call: triage fingerprint pass.  Second call: re-fingerprint
        # of the touched subset after Phase 2b.
        assert mocks["run_phase_3a"].call_count == 2

    def test_no_survivors_skips_phase_2b_and_refingerprint(self):
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(survivors=[])
        try:
            run_all_two_pass(FakeConfig())
        finally:
            _stop_all(patches)

        # Phase 2 only once (sample), Phase 3a only once.
        assert mocks["run_phase_2"].call_count == 1
        assert mocks["run_phase_3a"].call_count == 1
        # Reset is not called when there are no survivors.
        mocks["reset_fingerprint_state"].assert_not_called()

    def test_reset_fingerprint_state_called_with_survivors(self):
        from discovery.orchestrator import run_all_two_pass

        survivors = [101, 202, 303]
        patches, mocks = _patch_two_pass_orchestrator(survivors=survivors)
        try:
            run_all_two_pass(FakeConfig())
        finally:
            _stop_all(patches)

        mocks["reset_fingerprint_state"].assert_called_once()
        # signature: (engine, table_ids)
        call = mocks["reset_fingerprint_state"].call_args
        assert call.args[1] == survivors

    def test_full_phase_sequence(self):
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(survivors=[1])
        try:
            run_all_two_pass(FakeConfig())
        finally:
            _stop_all(patches)

        mocks["run_phase_1"].assert_called_once()
        assert mocks["run_phase_2"].call_count == 2
        assert mocks["run_phase_3a"].call_count == 2
        mocks["run_phase_3b"].assert_called_once()
        mocks["run_phase_4"].assert_called_once()
        mocks["run_phase_5"].assert_called_once()
        mocks["generate_all"].assert_called_once()

    def test_disk_cap_enforced_after_each_extract(self):
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(survivors=[7])
        try:
            run_all_two_pass(FakeConfig())
        finally:
            _stop_all(patches)

        # Once after Phase 2a, once after Phase 2b.
        assert mocks["enforce_disk_cap"].call_count == 2

    def test_skip_phases_propagates_to_run_all_two_pass(self):
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(survivors=[1])
        try:
            run_all_two_pass(
                FakeConfig(),
                skip_phases=["pii_scan", "report"],
            )
        finally:
            _stop_all(patches)

        mocks["run_phase_3b"].assert_not_called()
        mocks["generate_all"].assert_not_called()
        # Phase 4 still runs.
        mocks["run_phase_4"].assert_called_once()


class TestRunAllTwoPassExists:
    def test_run_all_two_pass_is_importable(self):
        from discovery.orchestrator import run_all_two_pass

        assert callable(run_all_two_pass)


class TestRunAllTwoPassResume:
    """
    Resume semantics: when Phase 5 has already succeeded, the orchestrator
    must NOT redo Phase 2b or re-fingerprint — Phase 5 itself is gated and
    won't re-validate, so the work would be CPU-wasted no-ops.
    """

    def test_validate_complete_skips_phase_2b_and_refingerprint(self):
        from discovery.orchestrator import run_all_two_pass

        patches, mocks = _patch_two_pass_orchestrator(
            complete={"validate"},
            survivors=[1, 2, 3],  # would otherwise trigger 2b
        )
        try:
            run_all_two_pass(FakeConfig())
        finally:
            _stop_all(patches)

        # Phase 5 short-circuits via _run_phase's is_complete guard.
        mocks["run_phase_5"].assert_not_called()
        # Phase 2 only ran once (the sample pass).
        assert mocks["run_phase_2"].call_count == 1
        # Phase 3a only ran once.
        assert mocks["run_phase_3a"].call_count == 1
        # Reset never called.
        mocks["reset_fingerprint_state"].assert_not_called()
        # _surviving_candidate_table_ids never called either.
        mocks["surviving_table_ids"].assert_not_called()
