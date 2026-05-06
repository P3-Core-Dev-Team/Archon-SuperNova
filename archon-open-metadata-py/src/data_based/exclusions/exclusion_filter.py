import re
from typing import NamedTuple


class _Pattern(NamedTuple):
    name: str
    regex: re.Pattern[str]


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


class ExclusionFilter:
    """
    Stage 8: Skip non-business-domain tables (logs, temp, backup,
    migrations) before any heavy analysis runs.  Pure regex set; no
    external dependencies.
    """

    @staticmethod
    def should_exclude(table_name: str) -> dict:
        for p in _PATTERNS:
            if p.regex.search(table_name):
                return {"excluded": True, "reason": p.name, "table": table_name}
        return {"excluded": False, "reason": None, "table": table_name}

    @staticmethod
    def filter_tables(table_names: list[str]) -> dict:
        """Bulk entry: split a list into (kept, excluded[]).  Each
        excluded entry carries the matching pattern name so the UI can
        show the user *why* a table was filtered out."""
        kept: list[str] = []
        excluded: list[dict] = []
        for t in table_names:
            r = ExclusionFilter.should_exclude(t)
            if r["excluded"]:
                excluded.append(r)
            else:
                kept.append(t)
        return {"kept": kept, "excluded": excluded}

    @staticmethod
    def patterns() -> list[dict]:
        return [{"name": p.name, "regex": p.regex.pattern} for p in _PATTERNS]
