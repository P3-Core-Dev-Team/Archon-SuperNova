"""
test_exclusions.py — Pure unit tests for exclusion patterns and type classification.

No DB or network required.
"""
from __future__ import annotations

import pytest

from discovery.exclusions import excluded_patterns, should_exclude


# ---------------------------------------------------------------------------
# Test matrix: (table_name, expected_excluded, expected_reason_contains)
# ---------------------------------------------------------------------------

EXCLUDED_CASES: list[tuple[str, bool, str | None]] = [
    # log_pattern: _log$
    ("access_log", True, "log_pattern"),
    ("error_log", True, "log_pattern"),
    ("userlog", False, None),          # no underscore before log
    ("log_entries", False, None),      # not at end

    # temp_pattern: ^temp_
    ("temp_users", True, "temp_pattern"),
    ("temp_", True, "temp_pattern"),
    ("my_temp_table", False, None),    # not at start

    # tmp_pattern: ^tmp_
    ("tmp_import", True, "tmp_pattern"),
    ("tmp_", True, "tmp_pattern"),
    ("my_tmp", False, None),

    # backup_pattern: _bak\d*$
    ("users_bak", True, "backup_pattern"),
    ("users_bak2024", True, "backup_pattern"),
    ("orders_bak123", True, "backup_pattern"),
    ("bak_users", False, None),        # not suffix

    # archive_pattern: _archive$
    ("customers_archive", True, "archive_pattern"),
    ("archive_data", False, None),     # not at end

    # events_pattern: _events$
    ("page_events", True, "events_pattern"),
    ("click_events", True, "events_pattern"),
    ("events", False, None),           # no underscore prefix
    ("events_stream", False, None),    # not at end

    # etl_pattern: ^etl_
    ("etl_customers", True, "etl_pattern"),
    ("etl_", True, "etl_pattern"),
    ("my_etl_pipeline", False, None),  # not at start

    # migrations_pattern: ^migrations$
    ("migrations", True, "migrations_pattern"),
    ("my_migrations", False, None),   # not exact match
    ("migrations_old", False, None),  # not exact match

    # Normal business tables — should NOT be excluded
    ("customers", False, None),
    ("orders", False, None),
    ("order_items", False, None),
    ("products", False, None),
    ("users", False, None),
    ("accounts", False, None),
]


@pytest.mark.parametrize("table_name,expected_excluded,expected_reason", EXCLUDED_CASES)
def test_should_exclude(
    table_name: str,
    expected_excluded: bool,
    expected_reason: str | None,
) -> None:
    excluded, reason = should_exclude(table_name)
    assert excluded == expected_excluded, (
        f"Table '{table_name}': expected excluded={expected_excluded}, got {excluded}"
    )
    if expected_reason is not None:
        assert reason == expected_reason, (
            f"Table '{table_name}': expected reason='{expected_reason}', got '{reason}'"
        )
    else:
        assert reason is None, (
            f"Table '{table_name}': expected no reason, got '{reason}'"
        )


def test_case_insensitivity() -> None:
    """Patterns should match regardless of case."""
    assert should_exclude("ACCESS_LOG")[0] is True
    assert should_exclude("TEMP_USERS")[0] is True
    assert should_exclude("ETL_LOAD")[0] is True
    assert should_exclude("MIGRATIONS")[0] is True


def test_excluded_patterns_returns_all() -> None:
    """excluded_patterns() should return exactly 8 entries."""
    patterns = excluded_patterns()
    assert len(patterns) == 8
    names = [p[0] for p in patterns]
    expected_names = [
        "log_pattern",
        "temp_pattern",
        "tmp_pattern",
        "backup_pattern",
        "archive_pattern",
        "events_pattern",
        "etl_pattern",
        "migrations_pattern",
    ]
    assert names == expected_names
