from typing import Any, Iterable, Optional


class CardinalityClassifier:
    """
    Stage 13: Eligibility filter + cardinality refinement classification.

    The live source-DB probe (running ``COUNT(DISTINCT)`` against the
    extraction service) is *not* part of this class — it's IO and so
    lives in the pipeline runner.  What's here is pure: given pre-probed
    counts, decide whether a relationship's cardinality should change.
    """

    @staticmethod
    def filter_eligible(
        relationships: Iterable[dict[str, Any]],
        confidence_floor: float = 0.85,
    ) -> list[dict[str, Any]]:
        """Keep only rows that are MANY_TO_ONE *and* meet the confidence
        floor.  Other kinds (PARTIAL, NO_RELATIONSHIP) are deliberately
        excluded because the probe response (child total + child
        distinct) doesn't carry orphan evidence to flip them safely."""
        out: list[dict[str, Any]] = []
        for r in relationships:
            c = r.get("confidence")
            if c is None or float(c) < confidence_floor:
                continue
            if r.get("cardinality") != "MANY_TO_ONE":
                continue
            out.append(r)
        return out

    @staticmethod
    def refine(
        rel: dict[str, Any],
        total_rows: int,
        distinct_count: int,
    ) -> Optional[str]:
        """Classify a probed result.  Returns the new cardinality value
        when the relationship should be refined, ``None`` if it stays
        unchanged.

        Rule: a MANY_TO_ONE FK whose live distinct count equals the
        live total flips to ONE_TO_ONE.  Any other shape stays as-is.
        """
        if rel.get("cardinality") != "MANY_TO_ONE":
            return None
        if total_rows <= 0 or distinct_count <= 0:
            return None
        if distinct_count == total_rows:
            return "ONE_TO_ONE"
        return None

    @staticmethod
    def refine_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bulk: each input row is ``{rel: {...}, total_rows, distinct_count}``;
        each output row carries ``original_cardinality`` and the
        classifier verdict (``new_cardinality`` may be null when no
        change).  Caller persists the new value if non-null."""
        out: list[dict[str, Any]] = []
        for r in rows:
            rel = r.get("rel", {})
            total = int(r.get("total_rows", 0))
            distinct = int(r.get("distinct_count", 0))
            verdict = CardinalityClassifier.refine(rel, total, distinct)
            out.append({
                "rel": rel,
                "total_rows": total,
                "distinct_count": distinct,
                "original_cardinality": rel.get("cardinality"),
                "new_cardinality": verdict,
            })
        return out
