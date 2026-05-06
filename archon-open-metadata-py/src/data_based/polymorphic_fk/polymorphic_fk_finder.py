import re
from typing import Optional


_TYPE_SUFFIX_RE = re.compile(r"^(.+?)_(type|kind)$", re.IGNORECASE)


class PolymorphicFkFinder:
    """
    Stage 15: Detects Rails / Django-style polymorphic FK columns —
    pairs of ``<x>_type`` and ``<x>_id`` where the type column carries
    a discriminator value naming the parent table.

    Pure: discriminator → candidate parent matching, name-aware
    pluralisation, confidence blending.  The DuckDB anti-join validation
    that confirms the (id, parent) referential integrity lives in the
    pipeline runner; what's here is the pattern recognition layer.
    """

    @staticmethod
    def split_type_prefix(col_name: str) -> Optional[str]:
        """Return the prefix of a ``<x>_type`` / ``<x>_kind`` column,
        else ``None``."""
        m = _TYPE_SUFFIX_RE.match(col_name)
        return m.group(1).lower() if m else None

    @staticmethod
    def singularize(name: str) -> str:
        n = name.lower()
        if len(n) > 3 and n.endswith("ies"):
            return n[:-3] + "y"
        if len(n) > 2 and n.endswith("es") and not n.endswith("ses"):
            return n[:-2]
        if len(n) > 1 and n.endswith("s") and not n.endswith("ss"):
            return n[:-1]
        return n

    @staticmethod
    def pluralize(name: str) -> str:
        n = name.lower()
        if n.endswith("y") and len(n) > 1 and n[-2] not in "aeiou":
            return n[:-1] + "ies"
        if n.endswith(("s", "x", "z")) or n.endswith(("sh", "ch")):
            return n + "es"
        return n + "s"

    @staticmethod
    def candidate_parent_names(value: str) -> list[str]:
        """Generate candidate parent-table names for a discriminator
        value (``"Post"`` → ``["post", "posts"]``)."""
        if not value:
            return []
        seen: list[str] = []
        base = PolymorphicFkFinder.singularize(value)
        plural = PolymorphicFkFinder.pluralize(base)
        for cand in (value.lower(), base, plural):
            if cand and cand not in seen:
                seen.append(cand)
        return seen

    @staticmethod
    def parent_name_match(value: str, table_name: str) -> bool:
        if not value or not table_name:
            return False
        tbl = table_name.split(".")[-1].lower()
        return tbl in PolymorphicFkFinder.candidate_parent_names(value)

    @staticmethod
    def confidence(
        name_match_strength: float,
        containment: float,
        distinct_count: int,
    ) -> float:
        """Blend signals into [0, 1].  0.7 containment, 0.2 name match
        strength, 0.1 distinct-count (saturating at 1000)."""
        distinct_score = min(distinct_count / 1000.0, 1.0)
        return round(
            0.7 * containment + 0.2 * name_match_strength + 0.1 * distinct_score, 4
        )

    @staticmethod
    def detect_columns(
        columns: list[dict],
    ) -> list[dict]:
        """Identify ``<x>_type`` / ``<x>_kind`` columns from a flat
        column-inventory list.  Each match's prefix is the polymorphic
        family name (e.g. ``commentable``)."""
        out: list[dict] = []
        for c in columns:
            cname = str(c.get("column", "") or c.get("column_name", ""))
            prefix = PolymorphicFkFinder.split_type_prefix(cname)
            if prefix is None:
                continue
            out.append({
                "table": c.get("table") or c.get("table_name"),
                "type_column": cname,
                "prefix": prefix,
            })
        return out

    @staticmethod
    def match_partitions(
        type_column_values: list[str],
        candidate_parents: list[str],
    ) -> dict:
        """Given the distinct values seen in a ``<x>_type`` column and a
        list of candidate parent table names, return per-partition
        matches: ``{matched: {value: parent}, unmatched: [value]}``."""
        matched: dict[str, str] = {}
        unmatched: list[str] = []
        for v in type_column_values:
            if not v:
                continue
            cands = PolymorphicFkFinder.candidate_parent_names(v)
            hit = None
            for parent in candidate_parents:
                pl = parent.split(".")[-1].lower()
                if pl in cands:
                    hit = parent
                    break
            if hit:
                matched[v] = hit
            else:
                unmatched.append(v)
        return {"matched": matched, "unmatched": unmatched}
