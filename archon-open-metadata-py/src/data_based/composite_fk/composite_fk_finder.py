import difflib
import itertools
from typing import Any


def _name_sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


class CompositeFkFinder:
    """
    Stage 14: Detects composite (multi-column) FK candidates from a list
    of single-column candidates that already share a (child_table,
    parent_table) pair.  Pure proposal / classification — the live
    DuckDB anti-join validation step lives in the caller.
    """

    @staticmethod
    def classify(
        cd: int, pd: int, orphans: int,
        containment_threshold: float = 0.95,
    ) -> dict:
        """Translate raw composite counts (child distinct, parent
        distinct, orphan tuples) into a (containment, cardinality)
        verdict.  Mirrors single-column classify so semantics match."""
        containment_full = (1.0 - orphans / cd) if cd > 0 else 0.0
        if cd == pd and orphans == 0:
            cardinality = "ONE_TO_ONE"
        elif orphans == 0 and cd < pd:
            cardinality = "MANY_TO_ONE"
        elif orphans > 0 and containment_full >= containment_threshold:
            cardinality = "PARTIAL"
        else:
            cardinality = "NO_RELATIONSHIP"
        return {
            "containment_full": round(containment_full, 4),
            "cardinality": cardinality,
        }

    @staticmethod
    def enumerate_subsets(singles: list[dict[str, Any]], arity: int) -> list[list[dict]]:
        if arity < 1 or arity > len(singles):
            return []
        return [list(t) for t in itertools.combinations(singles, arity)]

    @staticmethod
    def should_propose(
        constituent_containments: list[float],
        min_singles_containment: float = 0.95,
    ) -> bool:
        """A subset is worth proposing iff every single is already
        ``≥ min_singles_containment`` AND not every single is already
        a perfect FK (composite would be redundant)."""
        if not constituent_containments:
            return False
        if any(c < min_singles_containment for c in constituent_containments):
            return False
        if all(c >= 1.0 for c in constituent_containments):
            return False
        return True

    @staticmethod
    def name_similarity_floor(child_cols: list[str], parent_cols: list[str]) -> float:
        if len(child_cols) != len(parent_cols) or not child_cols:
            return 0.0
        return min(_name_sim(c, p) for c, p in zip(child_cols, parent_cols))

    @staticmethod
    def avg_name_similarity(child_cols: list[str], parent_cols: list[str]) -> float:
        if len(child_cols) != len(parent_cols) or not child_cols:
            return 0.0
        sims = [_name_sim(c, p) for c, p in zip(child_cols, parent_cols)]
        return sum(sims) / len(sims)

    @staticmethod
    def propose(
        singles: list[dict[str, Any]],
        max_arity: int = 3,
        min_singles_containment: float = 0.95,
        min_name_similarity_floor: float = 0.5,
    ) -> list[dict]:
        """Iterate every subset of ``singles`` of size 2..max_arity and
        return only those that pass the should_propose + name-similarity
        gates.  Each proposal carries the constituent rows, their per-pair
        name similarity, and the avg/min similarity scores."""
        proposals: list[dict] = []
        for arity in range(2, max_arity + 1):
            for subset in CompositeFkFinder.enumerate_subsets(singles, arity):
                cs = [float(r.get("containment_full", 0.0)) for r in subset]
                if not CompositeFkFinder.should_propose(cs, min_singles_containment):
                    continue
                child_cols = [str(r.get("child_col", "")) for r in subset]
                parent_cols = [str(r.get("parent_col", "")) for r in subset]
                floor = CompositeFkFinder.name_similarity_floor(child_cols, parent_cols)
                if floor < min_name_similarity_floor:
                    continue
                proposals.append({
                    "arity": arity,
                    "child_cols": child_cols,
                    "parent_cols": parent_cols,
                    "constituents": subset,
                    "name_sim_floor": round(floor, 4),
                    "name_sim_avg": round(
                        CompositeFkFinder.avg_name_similarity(child_cols, parent_cols), 4
                    ),
                })
        return proposals
