"""
exclusions.py — Table-name exclusion patterns.

Patterns are compiled once at import time.  Call should_exclude(table_name) to
check a single name; it returns a (excluded: bool, reason: str | None) tuple.

Regex patterns (per spec):
  _log$           → "log_pattern"
  ^temp_          → "temp_pattern"
  ^tmp_           → "tmp_pattern"
  _bak\\d*$       → "backup_pattern"
  _archive$       → "archive_pattern"
  _events$        → "events_pattern"
  ^etl_           → "etl_pattern"
  ^migrations$    → "migrations_pattern"
"""
from __future__ import annotations

import re
from typing import NamedTuple


class _Pattern(NamedTuple):
    name: str
    regex: re.Pattern[str]


# Compiled at import time. Patterns are deliberately permissive about
# trailing date / sequence suffixes (`_2024_q1`, `_001`, `_v3`) because
# real-world tables rarely end *exactly* on the family marker.
_PATTERNS: list[_Pattern] = [
    _Pattern("log_pattern", re.compile(r"_log(_.*)?$", re.IGNORECASE)),
    _Pattern("temp_pattern", re.compile(r"^temp_", re.IGNORECASE)),
    _Pattern("tmp_pattern", re.compile(r"^tmp_", re.IGNORECASE)),
    _Pattern("backup_pattern", re.compile(r"_bak(_.*|\d*)?$", re.IGNORECASE)),
    _Pattern("archive_pattern", re.compile(r"_archive(_.*)?$", re.IGNORECASE)),
    _Pattern("events_pattern", re.compile(r"_events(_.*)?$", re.IGNORECASE)),
    _Pattern("etl_pattern", re.compile(r"^etl_", re.IGNORECASE)),
    _Pattern("migrations_pattern", re.compile(r"^migrations(_.*|$)", re.IGNORECASE)),
]


def should_exclude(table_name: str) -> tuple[bool, str | None]:
    """
    Check whether *table_name* matches any exclusion pattern.

    Parameters
    ----------
    table_name:
        The bare table name (not schema-qualified).

    Returns
    -------
    (excluded, reason)
        excluded is True when the table should be skipped.
        reason is the pattern name when excluded, else None.
    """
    for p in _PATTERNS:
        if p.regex.search(table_name):
            return True, p.name
    return False, None


def excluded_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Return a copy of the (name, compiled_pattern) list for inspection/testing."""
    return [(p.name, p.regex) for p in _PATTERNS]
