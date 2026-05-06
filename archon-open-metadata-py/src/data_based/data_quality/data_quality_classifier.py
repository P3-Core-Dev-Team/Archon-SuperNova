from dataclasses import dataclass
from typing import Any, Optional


class IssueType:
    """Stable string constants for the data-quality issue catalogue.
    Add new values at the bottom; never remove or rename — values
    persist into downstream stores."""

    NULL_HEAVY = "NULL_HEAVY"
    ALL_NULL = "ALL_NULL"
    DUPLICATE_PK = "DUPLICATE_PK"
    LEADING_TRAILING_WHITESPACE = "LEADING_TRAILING_WHITESPACE"
    EMPTY_STRING = "EMPTY_STRING"
    MIXED_CASE = "MIXED_CASE"
    LOW_CARDINALITY = "LOW_CARDINALITY"


_SEVERITY_BY_TYPE: dict[str, str] = {
    IssueType.ALL_NULL: "HIGH",
    IssueType.DUPLICATE_PK: "HIGH",
    IssueType.NULL_HEAVY: "MEDIUM",
    IssueType.MIXED_CASE: "MEDIUM",
    IssueType.LEADING_TRAILING_WHITESPACE: "LOW",
    IssueType.EMPTY_STRING: "LOW",
    IssueType.LOW_CARDINALITY: "LOW",
}


@dataclass(frozen=True)
class ColumnMetrics:
    """One pass of per-column profiling output."""

    column_id: int
    column_name: str
    sample_rows: int
    null_count: int
    distinct_count: int
    is_pk: bool
    is_sole_pk: bool = False
    whitespace_count: Optional[int] = None
    empty_count: Optional[int] = None
    distinct_lower_count: Optional[int] = None
    samples_whitespace: Optional[list[str]] = None
    samples_mixed_case: Optional[list[str]] = None


class DataQualityClassifier:
    """
    Stage 7: Classifies per-column profiling metrics into a list of
    data-quality findings (NULL_HEAVY, DUPLICATE_PK, MIXED_CASE,
    LOW_CARDINALITY, etc.).  Pure: no DB / file IO.  The caller is
    responsible for producing ColumnMetrics — typically by running
    DuckDB profiling queries against extracted parquet.
    """

    @staticmethod
    def classify_metrics(
        m: ColumnMetrics,
        null_threshold: float = 0.50,
        low_card_floor: int = 5,
        low_card_min_rows: int = 1000,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        rows = max(1, m.sample_rows)

        # Null density
        null_frac = m.null_count / rows
        if m.null_count == m.sample_rows and m.sample_rows > 0:
            out.append({
                "column_id": m.column_id,
                "issue_type": IssueType.ALL_NULL,
                "severity": _SEVERITY_BY_TYPE[IssueType.ALL_NULL],
                "count": m.null_count,
                "sample_rows": m.sample_rows,
                "fraction": 1.0,
                "samples": [],
            })
        elif null_frac >= null_threshold and m.null_count > 0:
            out.append({
                "column_id": m.column_id,
                "issue_type": IssueType.NULL_HEAVY,
                "severity": _SEVERITY_BY_TYPE[IssueType.NULL_HEAVY],
                "count": m.null_count,
                "sample_rows": m.sample_rows,
                "fraction": round(null_frac, 4),
                "samples": [],
            })

        # Duplicate PK — only fires for SINGLE-column PKs.  Composite
        # members legitimately have duplicates per-column (uniqueness
        # is on the tuple, not the column).
        non_null = m.sample_rows - m.null_count
        if (
            m.is_pk
            and m.is_sole_pk
            and non_null > 0
            and m.distinct_count < non_null
        ):
            dup_count = non_null - m.distinct_count
            out.append({
                "column_id": m.column_id,
                "issue_type": IssueType.DUPLICATE_PK,
                "severity": _SEVERITY_BY_TYPE[IssueType.DUPLICATE_PK],
                "count": dup_count,
                "sample_rows": m.sample_rows,
                "fraction": round(dup_count / non_null, 4),
                "samples": [],
            })

        # Whitespace
        if m.whitespace_count is not None and m.whitespace_count > 0:
            out.append({
                "column_id": m.column_id,
                "issue_type": IssueType.LEADING_TRAILING_WHITESPACE,
                "severity": _SEVERITY_BY_TYPE[IssueType.LEADING_TRAILING_WHITESPACE],
                "count": m.whitespace_count,
                "sample_rows": m.sample_rows,
                "fraction": round(m.whitespace_count / rows, 4),
                "samples": list(m.samples_whitespace or [])[:3],
            })

        # Empty string
        if m.empty_count is not None and m.empty_count > 0:
            out.append({
                "column_id": m.column_id,
                "issue_type": IssueType.EMPTY_STRING,
                "severity": _SEVERITY_BY_TYPE[IssueType.EMPTY_STRING],
                "count": m.empty_count,
                "sample_rows": m.sample_rows,
                "fraction": round(m.empty_count / rows, 4),
                "samples": [],
            })

        # Mixed case
        if (
            m.distinct_lower_count is not None
            and m.distinct_count > 0
            and m.distinct_lower_count < m.distinct_count
        ):
            collision = m.distinct_count - m.distinct_lower_count
            out.append({
                "column_id": m.column_id,
                "issue_type": IssueType.MIXED_CASE,
                "severity": _SEVERITY_BY_TYPE[IssueType.MIXED_CASE],
                "count": collision,
                "sample_rows": m.sample_rows,
                "fraction": round(collision / max(m.distinct_count, 1), 4),
                "samples": list(m.samples_mixed_case or [])[:3],
            })

        # Low cardinality — skip PKs (expected unique) and tiny samples.
        if (
            not m.is_pk
            and m.sample_rows >= low_card_min_rows
            and 0 < m.distinct_count < low_card_floor
        ):
            out.append({
                "column_id": m.column_id,
                "issue_type": IssueType.LOW_CARDINALITY,
                "severity": _SEVERITY_BY_TYPE[IssueType.LOW_CARDINALITY],
                "count": m.distinct_count,
                "sample_rows": m.sample_rows,
                "fraction": round(m.distinct_count / m.sample_rows, 4),
                "samples": [],
            })

        return out

    @staticmethod
    def classify_batch(
        rows: list[dict[str, Any]],
        null_threshold: float = 0.50,
        low_card_floor: int = 5,
        low_card_min_rows: int = 1000,
    ) -> list[dict[str, Any]]:
        """Bulk entry point: takes a list of metric-dicts (matching
        ``ColumnMetrics`` field shape) and returns the flattened list of
        findings.  Used by ml_data_router so the FE can POST a JSON
        payload of profiling rows in one round-trip."""
        out: list[dict[str, Any]] = []
        for r in rows:
            m = ColumnMetrics(
                column_id=int(r.get("column_id", 0)),
                column_name=str(r.get("column_name", "")),
                sample_rows=int(r.get("sample_rows", 0)),
                null_count=int(r.get("null_count", 0)),
                distinct_count=int(r.get("distinct_count", 0)),
                is_pk=bool(r.get("is_pk", False)),
                is_sole_pk=bool(r.get("is_sole_pk", False)),
                whitespace_count=r.get("whitespace_count"),
                empty_count=r.get("empty_count"),
                distinct_lower_count=r.get("distinct_lower_count"),
                samples_whitespace=r.get("samples_whitespace"),
                samples_mixed_case=r.get("samples_mixed_case"),
            )
            out.extend(
                DataQualityClassifier.classify_metrics(
                    m,
                    null_threshold=null_threshold,
                    low_card_floor=low_card_floor,
                    low_card_min_rows=low_card_min_rows,
                )
            )
        return out
