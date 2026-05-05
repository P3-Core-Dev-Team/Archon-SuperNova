"""
test_cli.py — CLI smoke tests using Typer's CliRunner.

Strategy
--------
Every command is invoked with ``--dry-run`` so no IO occurs — no DB, no HTTP
client, no file system side effects.  The tests only assert:

1. The command exits with code 0.
2. The ``--help`` output contains the command name or a relevant keyword.

These tests are runnable without any sibling-agent modules installed.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from discovery.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def invoke_dry(*args: str) -> "typer.testing.Result":
    """Invoke the CLI with --dry-run and return the result."""
    return runner.invoke(app, list(args) + ["--dry-run"])


# ---------------------------------------------------------------------------
# Help tests — verify commands are registered
# ---------------------------------------------------------------------------


def test_app_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "discovery" in result.output.lower() or "pipeline" in result.output.lower()


@pytest.mark.parametrize(
    "command",
    [
        ["init", "--help"],
        ["inventory", "--help"],
        ["extract", "--help"],
        ["fingerprint", "--help"],
        ["pii-scan", "--help"],
        ["generate-candidates", "--help"],
        ["validate", "--help"],
        ["run-all", "--help"],
        ["status", "--help"],
        ["cleanup", "--help"],
        ["report", "--help"],
        ["report", "relationships", "--help"],
        ["report", "pii", "--help"],
        ["report", "exclusions", "--help"],
        ["report", "all", "--help"],
    ],
)
def test_help_exits_zero(command):
    result = runner.invoke(app, command)
    assert result.exit_code == 0, (
        f"Command {command!r} exited {result.exit_code}:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Dry-run tests — verify each command short-circuits cleanly
# ---------------------------------------------------------------------------


def test_init_dry_run():
    result = invoke_dry("init")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_inventory_dry_run():
    result = invoke_dry("inventory")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_extract_dry_run():
    result = invoke_dry("extract")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_extract_dry_run_with_limit():
    result = invoke_dry("extract", "--limit", "10")
    assert result.exit_code == 0, result.output
    assert "limit=10" in result.output or "dry-run" in result.output.lower()


def test_fingerprint_dry_run():
    result = invoke_dry("fingerprint")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_pii_scan_dry_run():
    result = invoke_dry("pii-scan")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_generate_candidates_dry_run():
    result = invoke_dry("generate-candidates")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_validate_dry_run():
    result = invoke_dry("validate")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_validate_dry_run_with_limit():
    result = invoke_dry("validate", "--limit", "5")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_run_all_dry_run():
    result = invoke_dry("run-all")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_run_all_dry_run_with_limit_and_skip():
    result = invoke_dry("run-all", "--limit", "3", "--skip", "fingerprint,pii_scan")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_status_dry_run():
    result = invoke_dry("status")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_cleanup_dry_run_default():
    result = invoke_dry("cleanup")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_cleanup_dry_run_keep_results():
    result = runner.invoke(app, ["cleanup", "--keep-results", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "preserve" in result.output.lower() or "dry-run" in result.output.lower()


def test_report_relationships_dry_run():
    result = invoke_dry("report", "relationships")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_report_pii_dry_run():
    result = invoke_dry("report", "pii")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_report_exclusions_dry_run():
    result = invoke_dry("report", "exclusions")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


def test_report_all_dry_run():
    result = invoke_dry("report", "all")
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()


# ---------------------------------------------------------------------------
# Global option propagation
# ---------------------------------------------------------------------------


def test_text_logs_flag_dry_run():
    result = runner.invoke(app, ["init", "--text-logs", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_log_level_flag_dry_run():
    result = runner.invoke(app, ["init", "--log-level", "DEBUG", "--dry-run"])
    assert result.exit_code == 0, result.output
