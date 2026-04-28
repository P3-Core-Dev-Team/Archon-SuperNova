"""
Phase 4 — FK candidate generation.

Owns BOTH the pure algorithm (sql_prefilter, faiss_lsh_search,
generate_candidates) and the Phase 4 orchestrator (run_phase_4).

Pure helpers have no SQLAlchemy / config / run_log imports.  The orchestrator
imports those at function-scope.

Step 4a — SQL pre-filter:
  Self-join of col_inventory on type-class compatibility + cardinality gates.
  Name similarity via difflib.SequenceMatcher (cheap, no extra deps).

Step 4b — FAISS LSH containment search:
  Load all column sketches into a FAISS IndexBinaryFlat index.
  FAISS is used as an ANN recall engine (Hamming on raw sketch bytes).
  Containment is then re-estimated via the Jaccard formula on the MinHash/
  HyperMinHash objects for every recalled pair.

Precision gates (added in the FK-precision improvement pass)
------------------------------------------------------------
* A1: candidate emission requires ``parent.is_pk`` or
  ``parent.is_unique_indexed``.  When the parent table has no PK / unique
  metadata at all (population miss in inventory, not a real data property),
  candidates are still emitted but flagged with ``parent_pk_unknown=True`` so
  downstream review can spot them.  Toggleable via
  ``config.relationships.require_parent_pk`` (default True).
* A2: id-to-id between unrelated tables is the dominant false-positive class.
  When *both* sides have ``is_pk=true``, the candidate is dropped from
  ``sql_prefilter`` unless the column-name similarity exceeds 0.7.
* A3: dense 1..N serials on both sides are heavily downweighted unless the
  names match meaningfully (used inside the multi-feature score).
* A4: multi-feature confidence — see :mod:`discovery.scoring`.
* A5: each candidate carries a ``tier`` string — ``primary`` (pass to Phase 5)
  or ``advisory_lowconf`` (audit only, Phase 5 skips).

Exports
-------
ColSketch           input dataclass
FkCandidate         output dataclass
sql_prefilter       step 4a (pure)
faiss_lsh_search    step 4b (pure)
generate_candidates convenience wrapper (pure)
run_phase_4         orchestrator
"""
from __future__ import annotations

import datetime as _dt
import difflib
import pickle
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

import structlog

from discovery.scoring import compute_confidence, is_dense_serial

# A3 helpers (name_similarity / is_self_ref_role / ROLE_SUFFIXES) may not yet
# be present in scoring.py.  Try optimistically, fall back to local
# implementations otherwise.  This way verification works today and the
# upgrade kicks in automatically once A3 lands.
try:  # pragma: no cover — exercised via runtime selection
    from discovery.scoring import (  # type: ignore[attr-defined]
        ROLE_SUFFIXES as _SCORING_ROLE_SUFFIXES,
    )
    from discovery.scoring import (  # type: ignore[attr-defined]
        is_self_ref_role as _scoring_is_self_ref_role,
    )
    from discovery.scoring import (  # type: ignore[attr-defined]
        name_similarity as _scoring_name_similarity,
    )

    _A3_AVAILABLE = True
except ImportError:  # pragma: no cover — fallback path
    _SCORING_ROLE_SUFFIXES = None  # type: ignore[assignment]
    _scoring_is_self_ref_role = None  # type: ignore[assignment]
    _scoring_name_similarity = None  # type: ignore[assignment]
    _A3_AVAILABLE = False

# Cross-table role-based FK helper (Sprint A7 #1).  Optional — fall back to
# always-False when the helper is not yet present in scoring.py.
try:  # pragma: no cover — exercised via runtime selection
    from discovery.scoring import (  # type: ignore[attr-defined]
        is_role_based_fk as _scoring_is_role_based_fk,
    )
except ImportError:  # pragma: no cover — fallback path
    _scoring_is_role_based_fk = None  # type: ignore[assignment]

# Generic <x>_id → <table>.id suffix-substring match (catches FK conventions
# that don't lexically clear the 0.85 name-sim bypass — e.g. names with
# extra prefixes like ``ads_st_config_policy``).  Optional fallback.
try:  # pragma: no cover — exercised via runtime selection
    from discovery.scoring import (  # type: ignore[attr-defined]
        is_suffix_id_match as _scoring_is_suffix_id_match,
    )
except ImportError:  # pragma: no cover — fallback path
    _scoring_is_suffix_id_match = None  # type: ignore[assignment]

# Optional semantic name similarity (A4).  May not be installed.
try:  # pragma: no cover — exercised via runtime selection
    from discovery.name_similarity import (  # type: ignore[import]
        SEMANTIC_AVAILABLE as _SEMANTIC_AVAILABLE,
    )
    from discovery.name_similarity import (  # type: ignore[import]
        best_similarity as _semantic_best_similarity,
    )
except ImportError:  # pragma: no cover — fallback path
    _SEMANTIC_AVAILABLE = False
    _semantic_best_similarity = None  # type: ignore[assignment]


if TYPE_CHECKING:
    from sqlalchemy import Engine

    from discovery.config import AppConfig

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Type compatibility rules (mirror col_inventory.type_class values)
# ---------------------------------------------------------------------------
_COMPATIBLE_TYPES: dict[str, frozenset[str]] = {
    "INT_NARROW": frozenset({"INT_NARROW", "INT_WIDE"}),
    "INT_WIDE":   frozenset({"INT_NARROW", "INT_WIDE"}),
    "UUID":       frozenset({"UUID"}),
    "STRING_SHORT": frozenset({"STRING_SHORT", "STRING_LONG"}),
    "STRING_LONG":  frozenset({"STRING_SHORT", "STRING_LONG"}),
    "DATE":       frozenset({"DATE"}),
    "TIMESTAMP":  frozenset({"TIMESTAMP"}),
}


def types_compatible(child_class: str, parent_class: str) -> bool:
    """Return True if child_class can FK into parent_class."""
    compatible = _COMPATIBLE_TYPES.get(child_class, frozenset())
    return parent_class in compatible


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ColSketch:
    """All metadata needed about one column for candidate generation.

    The trailing fields (``is_pk`` … ``null_pct``) were added to support the
    FK-precision gates.  They default to safe ``False`` / ``None`` values so
    existing positional callers (notably the unit-test fixtures) keep working
    without modification.
    """

    column_id: int
    table_id: int
    table_name: str
    column_name: str
    type_class: str
    distinct_count: int
    is_fk_eligible: bool
    sketch_blob: bytes

    # Added for FK-precision gates.  Backwards-compatible defaults.
    is_pk: bool = False
    is_unique_indexed: bool = False
    is_indexed: bool = False
    min_val: Any = None
    max_val: Any = None
    null_pct: Optional[float] = None
    # is_implicit_pk: derived in Phase 4 from the data itself
    # (distinct_count == row_count AND no nulls).  Lets the precision gate
    # work on databases that do NOT declare PRIMARY KEY constraints.
    is_implicit_pk: bool = False
    # ordinal_position: used as the final tiebreaker when picking the implicit
    # PK among multiple candidate columns inside one table (Tier1 #3).
    ordinal_position: Optional[int] = None
    # is_pii: cross-checked against pii_findings inside run_phase_4.  When True
    # the column cannot participate in any FK candidate.  Defaults to False so
    # existing positional callers are unaffected.
    is_pii: bool = False


@dataclass
class FkCandidate:
    """A proposed FK relationship for Phase 5 validation.

    ``tier``:
      * ``"primary"``           — passes the precision gates; Phase 5
                                  validates these.
      * ``"advisory_lowconf"``  — kept for audit only (low evidence).
                                  Phase 5 skips them.

    ``parent_pk_unknown`` flags candidates whose parent table had *no* PK or
    unique-index metadata at all.  Not persisted to the DB today; surfaced via
    debug logs and used by the tier classifier.
    """

    child_col_id: int
    parent_col_id: int
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    estimated_containment: float
    name_similarity: float
    type_match: bool
    source_stage: str
    joint_estimate: Optional[int]
    tier: str = "primary"
    parent_pk_unknown: bool = False
    score_features: dict[str, float] = field(default_factory=dict)
    # confidence: in-memory only, computed at emission time using the
    # multi-feature formula in scoring.compute_confidence.  Used by the
    # per-child top-K gate (#6) and global cap (#14); not persisted today
    # (fk_candidates table has no confidence column).
    confidence: float = 0.0
    # Sprint A7 #1: True iff this candidate matches a recognised role-FK
    # pattern (self-ref or cross-table role-suffix → PK target) AND the
    # parent has PK signal.  ``apply_top_k_per_child`` exempts such
    # candidates from demotion so a role-FK target with a low LSH/name
    # confidence isn't ranked off the primary list by random parents
    # with high LSH containment but no semantic relationship.  In-memory
    # only; not persisted to fk_candidates.
    role_fk_locked: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _name_similarity(a: str, b: str) -> float:
    """Plural-aware name similarity.

    Thin wrapper around :func:`discovery.scoring.name_similarity` (Tier 1+2 #1).
    When that helper is not yet present in scoring.py, fall back to the
    legacy difflib ratio so verification keeps working.

    Kept as a module-private helper for backwards compatibility — existing
    tests and callers still import ``_name_similarity`` from this module.
    """
    if _A3_AVAILABLE and _scoring_name_similarity is not None:
        return float(_scoring_name_similarity(a, b))
    # Legacy fallback — preserved exactly to avoid drift in unit tests.
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ---------------------------------------------------------------------------
# Self-referential FK role detection (Tier 1+2 #2)
# ---------------------------------------------------------------------------


_FALLBACK_ROLE_SUFFIXES: frozenset[str] = frozenset({
    "manager_id",
    "parent_id",
    "head_id",
    "owner_id",
    "supervisor_id",
    "referrer_id",
    "reports_to",
    "managed_by",
    "created_by",
    "updated_by",
    "approved_by",
})


def _is_self_ref_role(
    child_table: str,
    child_col: str,
    parent_table: str,
    parent_col: str,
) -> bool:
    """Return True if (child_col -> parent_col) on the same table looks like
    a self-referential role pointer (e.g. ``employee.manager_id -> employee.id``).

    Dispatches to ``scoring.is_self_ref_role`` when available; otherwise uses
    a small fallback suffix list that captures the most common patterns.
    """
    if _A3_AVAILABLE and _scoring_is_self_ref_role is not None:
        try:
            return bool(
                _scoring_is_self_ref_role(child_table, child_col, parent_table, parent_col)
            )
        except Exception:  # pragma: no cover — defensive
            pass
    # Fallback: child column name ends in a known role suffix and the parent
    # column name is exactly 'id' / '<table>_id'.
    cc = (child_col or "").lower()
    pc = (parent_col or "").lower()
    pt = (parent_table or "").lower()
    if pc not in {"id", f"{pt}_id"}:
        return False
    suffixes = _SCORING_ROLE_SUFFIXES if _SCORING_ROLE_SUFFIXES is not None else _FALLBACK_ROLE_SUFFIXES
    return any(cc == s or cc.endswith("_" + s) or cc.endswith(s) for s in suffixes)


def _is_role_based_fk(
    child_col: str,
    parent_table: str,
    parent_col: str,
) -> bool:
    """Cross-table role-based FK detector (Sprint A7 #1).

    True iff the child column has a known role-suffix name (``posted_by``,
    ``referrer_id``, ``approved_by``, ...) AND the parent column is the
    PK of the parent table (``id`` or ``<plural-norm-of-parent_table>_id``).

    Independent of whether the child and parent tables match — this is the
    cross-table counterpart of :func:`_is_self_ref_role`.  Used inside the
    candidate gates to bypass the lexical name-similarity check when a
    role-suffix child is referencing a recognised PK target.
    """
    if _scoring_is_role_based_fk is not None:
        try:
            return bool(_scoring_is_role_based_fk(child_col, parent_table, parent_col))
        except Exception:  # pragma: no cover — defensive
            pass
    # Fallback: same logic as scoring.is_role_based_fk, used when scoring.py
    # has not yet been upgraded to expose the helper.
    cc = (child_col or "").lower()
    pc = (parent_col or "").lower()
    pt = (parent_table or "").lower()
    suffixes = _SCORING_ROLE_SUFFIXES if _SCORING_ROLE_SUFFIXES is not None else _FALLBACK_ROLE_SUFFIXES
    if cc not in suffixes:
        return False
    pt_norm = pt.rstrip("s") if pt.endswith("s") and not pt.endswith("ss") else pt
    return pc in {"id", f"{pt_norm}_id"}


def _is_suffix_id_match(
    child_col: str,
    parent_table: str,
    parent_col: str,
) -> bool:
    """Generic ``<x>_id`` → ``<table>.id`` suffix+substring detector.

    Returns True when the child column ends in ``_id`` and its prefix has
    a substring overlap with the parent table name (either side a substring
    of the other).  Catches the universal Postgres convention for any
    naming scheme — including ones with extra prefixes that drop the
    SequenceMatcher score below the 0.85 lexical bypass.

    Loose by design: data-level containment in Phase 5 is the real
    precision gate.  Use only inside the role-FK bypass logic so this
    rule expands the candidate pool, never the final tier.
    """
    if _scoring_is_suffix_id_match is not None:
        try:
            return bool(_scoring_is_suffix_id_match(child_col, parent_table, parent_col))
        except Exception:  # pragma: no cover - defensive
            pass
    # Inline fallback if scoring.py is older.
    cc = (child_col or "").lower()
    if not cc.endswith("_id") or len(cc) <= 3:
        return False
    pc = (parent_col or "").lower()
    pt = (parent_table or "").lower()
    pt_norm = pt.rstrip("s") if pt.endswith("s") and not pt.endswith("ss") else pt
    if pc not in {"id", f"{pt_norm}_id"}:
        return False
    prefix = cc[:-3]
    if not prefix or prefix == "id":
        return False
    return prefix in pt_norm or pt_norm in prefix


# ---------------------------------------------------------------------------
# Range overlap gate (Tier 1+2 #9)
# ---------------------------------------------------------------------------


def _coerce_int_or_none(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v != int(v):
            return None
        return int(v)
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _coerce_date_or_none(v: Any) -> Optional[_dt.date]:
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v).strip()[:10])
    except (TypeError, ValueError):
        return None


_INT_TYPE_CLASSES: frozenset[str] = frozenset({"INT_NARROW", "INT_WIDE"})
_DATE_TYPE_CLASSES: frozenset[str] = frozenset({"DATE"})


def _range_contained(child: "ColSketch", parent: "ColSketch") -> Optional[bool]:
    """Return True/False/None for the "child range fits inside parent range"
    sanity check.

    None: cannot compare (missing values, unparseable, or unsupported type).
    True: child.min >= parent.min AND child.max <= parent.max.
    False: an explicit out-of-range value was observed (caller should reject).

    Tier 1+2 #9.  Skipped (None) for non-INT/non-DATE classes to avoid
    spurious rejections on strings and UUIDs.
    """
    if child.min_val is None or child.max_val is None:
        return None
    if parent.min_val is None or parent.max_val is None:
        return None

    if child.type_class in _INT_TYPE_CLASSES and parent.type_class in _INT_TYPE_CLASSES:
        cmin = _coerce_int_or_none(child.min_val)
        cmax = _coerce_int_or_none(child.max_val)
        pmin = _coerce_int_or_none(parent.min_val)
        pmax = _coerce_int_or_none(parent.max_val)
        if None in (cmin, cmax, pmin, pmax):
            return None
        return cmin >= pmin and cmax <= pmax  # type: ignore[operator]

    if child.type_class in _DATE_TYPE_CLASSES and parent.type_class in _DATE_TYPE_CLASSES:
        cmin = _coerce_date_or_none(child.min_val)
        cmax = _coerce_date_or_none(child.max_val)
        pmin = _coerce_date_or_none(parent.min_val)
        pmax = _coerce_date_or_none(parent.max_val)
        if None in (cmin, cmax, pmin, pmax):
            return None
        return cmin >= pmin and cmax <= pmax  # type: ignore[operator]

    return None


# ---------------------------------------------------------------------------
# Hard dense-serial rejection (Tier 1+2 #7)
# ---------------------------------------------------------------------------


def _both_dense_serial(child: "ColSketch", parent: "ColSketch") -> bool:
    if child.type_class not in _INT_TYPE_CLASSES:
        return False
    if parent.type_class not in _INT_TYPE_CLASSES:
        return False
    return is_dense_serial(
        child.distinct_count, child.null_pct, child.type_class,
        child.min_val, child.max_val,
    ) and is_dense_serial(
        parent.distinct_count, parent.null_pct, parent.type_class,
        parent.min_val, parent.max_val,
    )


# Below this name similarity, two dense 1..N serials are unrelated noise;
# reject outright rather than emit + downweight.  Tuned empirically: 0.4 is
# tight enough to drop "alpha" vs "omega" while keeping "id" vs "user_id".
_DENSE_SERIAL_REJECT_NAMESIM: float = 0.4


def _containment_from_jaccard(
    jaccard: float, child_distinct: int, parent_distinct: int
) -> float:
    if child_distinct <= 0:
        return 0.0
    union_est = (
        (child_distinct + parent_distinct) / (1.0 + jaccard)
        if (1.0 + jaccard) > 0
        else 0
    )
    intersection_est = jaccard * union_est
    containment = intersection_est / child_distinct
    return min(1.0, max(0.0, containment))


def _jaccard_from_sketches(sk1: object, sk2: object) -> float:
    try:
        return float(sk1.jaccard(sk2))  # type: ignore[attr-defined]
    except AttributeError:
        return 0.0


def _sketch_to_binary_vector(sk: object, vec_bytes: int = 2048) -> bytes:
    """
    Convert a sketch to a fixed-length byte string for FAISS IndexBinaryFlat.

    For MinHash with 256 permutations × 8 bytes/permutation = 2048 bytes.
    Use the full hashvalues array (no truncation/quantisation).

    For HyperMinHash or sketches without hashvalues, fall back to pickle bytes.
    """
    try:
        import numpy as np  # noqa: PLC0415

        # MinHash: hashvalues is a uint64 numpy array.  Use the full bytes.
        arr = sk.hashvalues  # type: ignore[attr-defined]
        raw = arr.tobytes()
    except AttributeError:
        raw = pickle.dumps(sk)

    if len(raw) >= vec_bytes:
        return raw[:vec_bytes]
    return raw + b"\x00" * (vec_bytes - len(raw))


def _parent_pk_table_set(cols: list[ColSketch]) -> set[int]:
    """Return the set of table_ids whose columns include any is_pk,
    is_unique_indexed, or is_implicit_pk flag.  Used for the A1 fallback so
    we don't outright reject a parent whose table simply lacks PK metadata
    in col_inventory.
    """
    seen: set[int] = set()
    for c in cols:
        if c.is_pk or c.is_unique_indexed or c.is_implicit_pk:
            seen.add(c.table_id)
    return seen


# FK-eligible type classes that can plausibly be a primary key.  Booleans /
# floats / long text are excluded — they're never declared as PKs in
# practice and are common false-positive shapes (a `is_active` boolean
# trivially has 2 distinct values).
_PK_ELIGIBLE_TYPE_CLASSES: frozenset[str] = frozenset({
    "INT_NARROW", "INT_WIDE", "UUID", "STRING_SHORT", "DATE", "TIMESTAMP",
})


def _singularize_table_name(name: str) -> str:
    """Tiny inline singularizer used as a tiebreaker for implicit-PK selection.

    Just strips a trailing ``s`` (case-insensitive).  We deliberately don't
    pull in a heavier inflection library — the only goal is to recognise
    ``customer_id`` as a sensible PK candidate inside a ``customers`` table.
    """
    if not name:
        return name
    if name.lower().endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if name.lower().endswith("s") and not name.lower().endswith("ss"):
        return name[:-1]
    return name


def _select_table_implicit_pk(
    cols_for_table: list[ColSketch],
) -> Optional[ColSketch]:
    """Pick the single best implicit-PK column for one table.

    Selection priority (Tier 1+2 #3):
      1. column already has ``is_pk=True`` (declared) wins
      2. column with ``is_unique_indexed=True`` wins
      3. column named exactly ``id`` (case-insensitive)
      4. column named ``<table_name>_id`` (case-insensitive)
      5. column named ``<singular_table_name>_id``
      6. column with smallest ``ordinal_position``

    The caller filters the input to columns that already passed the
    "distinct == row_count" detector — this helper just orders them.
    """
    if not cols_for_table:
        return None

    # 1. declared PK
    for c in cols_for_table:
        if c.is_pk:
            return c
    # 2. unique-indexed
    for c in cols_for_table:
        if c.is_unique_indexed:
            return c
    # 3. literal 'id'
    for c in cols_for_table:
        if (c.column_name or "").lower() == "id":
            return c
    # 4. <table>_id
    table_name = (cols_for_table[0].table_name or "").lower()
    target = f"{table_name}_id"
    for c in cols_for_table:
        if (c.column_name or "").lower() == target:
            return c
    # 5. <singular>_id
    singular = _singularize_table_name(table_name)
    if singular and singular != table_name:
        target_s = f"{singular}_id"
        for c in cols_for_table:
            if (c.column_name or "").lower() == target_s:
                return c
    # 6. smallest ordinal_position (None sorts last)
    def _ord_key(c: ColSketch) -> tuple[int, int, int]:
        op = c.ordinal_position if c.ordinal_position is not None else 10**9
        return (op, c.column_id, 0)

    return sorted(cols_for_table, key=_ord_key)[0]


def detect_implicit_pks(
    cols: list[ColSketch],
    table_row_counts: dict[int, int],
    *,
    min_table_rows: int = 2,
    null_tolerance: float = 0.01,
    distinct_tolerance: float = 0.03,
) -> int:
    """
    Mark columns as ``is_implicit_pk = True`` when the data itself shows
    they're de-facto primary keys.

    A column is an implicit PK if:
      * its table has at least ``min_table_rows`` rows (Sprint A7: lowered
        from 10 → 2 so tiny lookup tables — ``regions``, ``shifts``,
        ``employment_types``, ``review_cycles``, etc. — pick up an
        implicit-PK signal too.  Without this, low-cardinality FKs into
        these tables get rejected by the parent-PK gate as
        ``parent_pk_unknown`` and tiered to advisory).
      * its ``distinct_count`` equals the table's ``row_count`` to within
        ``distinct_tolerance`` (default 3%; HLL estimation noise on a
        14-bit register is ~0.8% standard error, with tail values reaching
        1-2%, so we leave headroom or true PKs get rejected for being
        50K/49K-style undercounts);
      * its ``null_pct`` is at most ``null_tolerance`` (default 1%);
      * its ``type_class`` is one of the PK-plausible families
        (no booleans / floats / long-text).

    Tier 1+2 #3 — *post-step*: at most ONE column per table is kept as
    ``is_implicit_pk=True``.  Sibling columns that ALSO satisfy the data
    test (distinct == row_count) are kept ``is_unique_indexed=True`` so
    they remain valid as FK *parents*, but have ``is_implicit_pk=False``
    so they don't trigger the A2 "both PK" trap as *children*.

    Mutates the ``cols`` list in place; returns the number of columns
    flagged as implicit PK after the per-table reconciliation.  Idempotent.
    """
    qualifying: dict[int, list[ColSketch]] = {}

    for c in cols:
        # Already a known PK / unique-indexed — the parent-PK gate already
        # has its signal from those flags, so we leave them alone (they're
        # not in the qualifying pool that gets reconciled below).
        if c.is_pk or c.is_unique_indexed:
            continue
        rc = table_row_counts.get(c.table_id)
        if rc is None or rc < min_table_rows:
            continue
        if c.distinct_count is None:
            continue
        # Distinct must equal row count (within tolerance).
        if abs(c.distinct_count - rc) / max(rc, 1) > distinct_tolerance:
            continue
        # No nulls (within tolerance).
        if c.null_pct is not None and c.null_pct > null_tolerance:
            continue
        # Type class must be PK-plausible.
        if c.type_class not in _PK_ELIGIBLE_TYPE_CLASSES:
            continue
        qualifying.setdefault(c.table_id, []).append(c)

    flagged = 0
    for table_id, table_cols in qualifying.items():
        # Pre-existing declared PKs in this table — if any, they already
        # cover the parent-PK signal.  Mark the one with the highest priority
        # as implicit_pk for symmetry (no harm — it was already is_pk anyway).
        chosen = _select_table_implicit_pk(table_cols)
        if chosen is None:
            continue
        chosen.is_implicit_pk = True
        flagged += 1
        # Demote the losers: still unique (distinct == row_count) but NOT
        # the table's primary identifier.  Marking them is_unique_indexed
        # keeps them valid as FK parents while preventing the A2 "both PK"
        # trap from dropping legitimate FK *children*.
        for c in table_cols:
            if c is chosen:
                continue
            c.is_implicit_pk = False
            c.is_unique_indexed = True
    return flagged


# ---------------------------------------------------------------------------
# Reverse-direction reconciliation (Tier 1+2 #5)
# ---------------------------------------------------------------------------


def _reconcile_pk_direction(cols: list[ColSketch]) -> int:
    """For (child, parent) pairs where BOTH are implicit_pk, demote whichever
    side has fewer inbound name-matches (``<table>_id``) elsewhere.

    Sprint A7 #4: tighter guards.

      * Reconciliation runs ONLY between two ``is_implicit_pk=True``
        sides.  A column with ``is_pk=True`` (declared) is authoritative
        and is never demoted, never compared against an implicit PK.
        (``detect_implicit_pks`` already excludes declared PKs from the
        ``is_implicit_pk`` set, so this is a defence-in-depth.)
      * Tie-breaker on equal inbound counts: prefer the column literally
        named ``id`` (the canonical PK convention), then the column
        named ``<table>_id``.  If neither side wins by name, leave both
        alone (previous behaviour).

    Returns the number of columns demoted.  Mutates ``cols`` in place.
    """
    # Implicit-PK candidates: exclude any with declared is_pk=True
    # (extra safety; detect_implicit_pks shouldn't set is_implicit_pk on
    # those, but defensive against direct callers in tests).
    implicit_pk_cols = [
        c for c in cols if c.is_implicit_pk and not c.is_pk
    ]
    if len(implicit_pk_cols) < 2:
        return 0

    # Pre-compute, per table, how many other tables have a column whose name
    # matches "<table>_id" or "<singular_table>_id".
    name_inbound: dict[int, int] = {}
    name_to_table_ids: dict[str, set[int]] = {}
    for c in cols:
        nm = (c.column_name or "").lower()
        name_to_table_ids.setdefault(nm, set()).add(c.table_id)

    for ipk in implicit_pk_cols:
        tname = (ipk.table_name or "").lower()
        targets: set[str] = {f"{tname}_id"}
        sing = _singularize_table_name(tname)
        if sing and sing != tname:
            targets.add(f"{sing}_id")
        # Count tables (excluding the parent's own table) where any of the
        # target names appears.
        seen_tables: set[int] = set()
        for t in targets:
            for tid in name_to_table_ids.get(t, set()):
                if tid != ipk.table_id:
                    seen_tables.add(tid)
        name_inbound[ipk.column_id] = len(seen_tables)

    def _name_priority(c: ColSketch) -> int:
        """Lower value = stronger PK-name evidence.

        0 — column literally named ``id``.
        1 — column named ``<table>_id`` or ``<singular(table)>_id``.
        2 — anything else.
        """
        cn = (c.column_name or "").lower()
        if cn == "id":
            return 0
        tname = (c.table_name or "").lower()
        sing = _singularize_table_name(tname)
        if cn in {f"{tname}_id", f"{sing}_id"}:
            return 1
        return 2

    # Pairwise: for every (a, b) where a/b are both implicit_pk and types
    # compatible, the side with FEWER inbound matches gets demoted.  We
    # only demote — never re-promote — so order of iteration is stable.
    demoted_ids: set[int] = set()
    by_id: dict[int, ColSketch] = {c.column_id: c for c in implicit_pk_cols}

    ipk_list = sorted(implicit_pk_cols, key=lambda c: c.column_id)
    for i, a in enumerate(ipk_list):
        if a.column_id in demoted_ids:
            continue
        for b in ipk_list[i + 1 :]:
            if b.column_id in demoted_ids:
                continue
            if a.table_id == b.table_id:
                continue
            if not types_compatible(a.type_class, b.type_class):
                continue
            # Defence-in-depth: never demote a side whose ``is_pk``
            # flag is set.  (Filtered above too; belt + braces.)
            if a.is_pk and b.is_pk:
                continue
            ia = name_inbound.get(a.column_id, 0)
            ib = name_inbound.get(b.column_id, 0)
            if ia != ib:
                loser = b if ia > ib else a
            else:
                # Tie on inbound count — break by PK-name convention.
                pa, pb = _name_priority(a), _name_priority(b)
                if pa == pb:
                    # No naming preference either; leave both untouched.
                    continue
                loser = a if pa > pb else b
            loser.is_implicit_pk = False
            loser.is_unique_indexed = True
            demoted_ids.add(loser.column_id)

    # Sanity: ensure all demoted cols have unique flag set.
    for col_id in demoted_ids:
        c = by_id.get(col_id)
        if c is not None and not c.is_unique_indexed:
            c.is_unique_indexed = True
    return len(demoted_ids)


# Threshold above which an id->id pair (both is_pk) is allowed despite the
# A2 asymmetric gate.  Tuned against the empirical data: 45% of FPs in HR are
# id<->id between unrelated tables and uniformly have name_sim ≈ 0.5, so 0.7
# is a tight cut.
_A2_NAME_SIM_THRESHOLD: float = 0.7

# Tighter threshold for the "both columns are declared PKs" subset.  An
# inheritance / IS-A pattern (``vendor.business_entity_id`` ↔
# ``business_entity.business_entity_id``) has name_sim = 1.0 so it passes;
# coincidental same-name PKs across unrelated tables (typical 0.5..0.8
# difflib ratios on ``id`` ↔ ``id``-shaped columns) are demoted.  Used in
# ``_classify_tier`` and in the both-PK admission gate inside
# ``sql_prefilter``.
_A2_BOTH_PK_NAME_SIM_THRESHOLD: float = 0.85

# Above this name similarity OR containment we bypass advisory tier entirely.
_PRIMARY_NAME_SIM: float = 0.6
_PRIMARY_CONTAINMENT: float = 0.9

# Minimum distinct values required for a child column when admitted via the
# role-FK bypass.  Set to 1 so single-value FKs (very common in tiny / sparse
# schemas where a child column has rows but they all reference the same
# parent row) are still admitted as primary candidates.  Phase-5 data
# containment is the actual precision gate — by Phase 4 we just need to
# generate the candidate.  Constant-column false positives are then rejected
# by Phase 5 via the parent-PK signal + name match anyway.
_ROLE_BYPASS_MIN_DISTINCT: int = 1


def _classify_tier(
    *,
    child: ColSketch,
    parent: ColSketch,
    estimated_containment: float,
    name_similarity: float,
    parent_pk_unknown: bool,
) -> str:
    """
    Tier classifier for the two-stage gating (task A5).

    primary:
        passes A1+A2+A3 gates AND
        (name_similarity > _PRIMARY_NAME_SIM OR
         estimated_containment > _PRIMARY_CONTAINMENT)

    advisory_lowconf:
        passes type/cardinality but lacks strong evidence.
    """
    # A1 unknown — lack of metadata is itself low evidence.
    if parent_pk_unknown:
        return "advisory_lowconf"

    # A2 — id->id with weak name match.  If both are PKs we keep them only
    # when the names align tightly (orders.id -> shipments.id with name match
    # would still survive — that's intentional).
    #
    # When *both* sides are declared is_pk=true the bar is raised to
    # ``_A2_BOTH_PK_NAME_SIM_THRESHOLD`` (0.85): this targets the inheritance
    # / IS-A pattern (``vendor.business_entity_id`` ↔
    # ``business_entity.business_entity_id``, name_sim = 1.0) while filtering
    # coincidental same-name PKs across unrelated tables.
    if child.is_pk and parent.is_pk and name_similarity <= _A2_BOTH_PK_NAME_SIM_THRESHOLD:
        return "advisory_lowconf"

    # A3 — both columns look like dense 1..N serials and the name doesn't
    # back the relationship → advisory.
    #
    # Bypass the demotion when the parent has a strong PK signal AND we have
    # near-full containment AND the names share *some* signal: FKs into small
    # lookup tables (``cycle_id`` → ``review_cycles.id``, ``plan_id`` →
    # ``benefit_plans.id``) are dense-1..N on both sides by construction, but
    # the FK is real.  Without this carve-out, accurate min/max ranges trip
    # the dense-serial demotion on every such pair.
    child_serial = is_dense_serial(
        child.distinct_count, child.null_pct, child.type_class,
        child.min_val, child.max_val,
    )
    parent_serial = is_dense_serial(
        parent.distinct_count, parent.null_pct, parent.type_class,
        parent.min_val, parent.max_val,
    )
    parent_pk_strong = parent.is_pk or parent.is_unique_indexed or parent.is_implicit_pk
    if child_serial and parent_serial and name_similarity <= 0.6:
        if not (
            parent_pk_strong
            and estimated_containment >= 0.95
            and name_similarity > 0.3
        ):
            return "advisory_lowconf"

    if (
        name_similarity > _PRIMARY_NAME_SIM
        or estimated_containment > _PRIMARY_CONTAINMENT
    ):
        return "primary"

    return "advisory_lowconf"


# ---------------------------------------------------------------------------
# Step 4a — SQL pre-filter
# ---------------------------------------------------------------------------


def sql_prefilter(
    cols: list[ColSketch],
    parent_distinct_ratio_min: float = 0.95,
    child_min_distinct_count: int = 100,
    require_parent_pk: bool = True,
    low_cardinality_name_sim_bypass: float = 0.85,
) -> list[FkCandidate]:
    """In-memory pre-filter mirroring the SQL self-join in the spec.

    Parameters
    ----------
    require_parent_pk:
        When True (default), the parent column must have ``is_pk`` or
        ``is_unique_indexed`` set.  Tables that have no such metadata at all
        are exempt — see ``parent_pk_unknown`` flag on the resulting
        candidate.
    low_cardinality_name_sim_bypass:
        FKs into very small lookup tables (<``child_min_distinct_count``
        distinct values) are normally filtered out — at that scale containment
        alone can't distinguish a real FK from coincidence.  This gate
        re-admits low-cardinality candidates *only* when the column name
        similarity is high (default 0.85) AND the parent is a PK / unique
        index.  That captures `phone_number_type_id -> phone_number_type.id`-
        shaped FKs into tiny enums without flooding noise from generic
        boolean / status columns.
    """
    # We no longer pre-filter eligibility by distinct_count: the cardinality
    # gate is applied per-pair below, with a name-similarity bypass for the
    # genuine "FK into a tiny lookup" case.
    # PII gating is folded into ``is_fk_eligible`` upstream — non-structural
    # PII columns have it cleared in run_phase_4; structural keys (PK / unique
    # index / id-named) keep it set even when PII findings hit.
    eligible = [c for c in cols if c.is_fk_eligible]

    parent_pk_tables = _parent_pk_table_set(cols) if require_parent_pk else set()

    candidates: list[FkCandidate] = []
    seen: set[tuple[int, int]] = set()

    for child in eligible:
        for parent in eligible:
            if child.column_id == parent.column_id:
                continue
            # NB: self-referential FKs are allowed (e.g. categories.parent_id ->
            # categories.id, employee_records.manager_id -> employee_records.id).
            # Only the same column referring to itself is rejected (above).
            pair = (child.column_id, parent.column_id)
            if pair in seen:
                continue

            if not types_compatible(child.type_class, parent.type_class):
                continue

            # ---- A1: parent must be a PK / unique-indexed / implicit-PK ----
            parent_pk_unknown = False
            parent_has_pk_signal = (
                parent.is_pk or parent.is_unique_indexed or parent.is_implicit_pk
            )
            if require_parent_pk and not parent_has_pk_signal:
                # Allow a parent whose entire table has no PK metadata at
                # all (likely an inventory population miss); flag it.
                if parent.table_id in parent_pk_tables:
                    continue
                parent_pk_unknown = True

            # Distinct-count gates.  Treat ``None`` as "unknown" — admit and
            # let downstream signals (PK match, name match) decide.  Without
            # this guard, very small / sparse schemas where the fingerprint
            # phase couldn't compute distinct_count get every candidate
            # silently dropped here.
            cd = child.distinct_count
            pd = parent.distinct_count
            if cd is not None and pd is not None and cd > pd * 1.05:
                continue
            if (
                parent_distinct_ratio_min > 0
                and cd is not None and pd is not None
                and pd < cd * parent_distinct_ratio_min
            ):
                continue

            name_sim = _name_similarity(child.column_name, parent.column_name)

            # ---- Sprint A7 #1: parent-table-aware name similarity --------
            # When the parent column is named simply 'id', `name_similarity`
            # alone misses the obvious match: name_similarity('region_id',
            # 'id') ≈ 0.36, even though `name_similarity('region_id',
            # 'regions.id')` ≈ 1.0.  Compute the qualified form and use
            # whichever is higher so candidates are not blocked by gates
            # that compare against the bare column name.
            qualified_name_sim = _name_similarity(
                child.column_name,
                f"{parent.table_name}.{parent.column_name}",
            )
            effective_name_sim = max(name_sim, qualified_name_sim)

            # Tier 1+2 #2 / Sprint A7 #1: role-suffix bypasses.
            # ``self_ref_role`` — same-table role pointer (manager_id → id).
            # ``role_based_fk`` — cross-table role pointer (posted_by →
            # employees.id, referrer_id → employees.id, ...).  Without the
            # cross-table bypass these otherwise-valid FKs are dropped:
            # role-suffix names share too few characters with the bare PK
            # name to clear any of the lexical gates.
            self_ref_role = (
                child.table_id == parent.table_id
                and _is_self_ref_role(
                    child.table_name, child.column_name,
                    parent.table_name, parent.column_name,
                )
            )
            cross_role_fk = _is_role_based_fk(
                child.column_name, parent.table_name, parent.column_name,
            )
            # Generic <x>_id → <table>.id suffix-substring rule — catches
            # FK conventions whose lexical similarity is too low for the
            # 0.85 bypass (e.g. ``storage_policy_id`` vs ``ads_st_config_policy``).
            suffix_id_match = _is_suffix_id_match(
                child.column_name, parent.table_name, parent.column_name,
            )
            # Combined "this is a recognised role-FK pattern" flag.  Self-
            # refs, cross-table role pointers, and generic <x>_id → <t>.id
            # all benefit from the same downstream gate-bypass treatment.
            role_bypass = (
                (self_ref_role or cross_role_fk or suffix_id_match)
                and parent_has_pk_signal
            )

            # ---- Tier 1+2 #7: hard reject dense-serial pairs that don't
            # share a meaningful name.  Role-FK patterns get a free pass:
            # `posted_by → employees.id` lands in the dense-serial trap
            # because both columns are 1..N integer surrogate keys, but the
            # relationship is real.
            if (
                _both_dense_serial(child, parent)
                and effective_name_sim < _DENSE_SERIAL_REJECT_NAMESIM
                and not role_bypass
            ):
                continue

            # ---- Tier 1+2 #9: range overlap as a hard signal.  Reject only
            # on an explicit False; None (cannot compare) keeps the candidate.
            range_ok = _range_contained(child, parent)
            if range_ok is False:
                continue

            # ---- Cardinality floor with name-similarity bypass ----------
            # Low-cardinality (<floor) columns are usually statistical noise;
            # filter them out UNLESS the column name strongly suggests the FK
            # AND the parent has PK/unique signal.  Captures real FKs into
            # tiny enum tables (phone_number_type, shift, etc.) without
            # admitting boolean / status-flag noise from arbitrary columns.
            child_dist = child.distinct_count if child.distinct_count is not None else 0
            if child_dist < child_min_distinct_count:
                # Role-FK bypass: same/cross-table role pointers with a known
                # role suffix OR generic <x>_id → <table>.id substring match,
                # with a recognised PK target on the parent.  Floor drops to
                # ``_ROLE_BYPASS_MIN_DISTINCT`` so role-FKs into tiny tables
                # are recovered.
                if role_bypass:
                    if child_dist < _ROLE_BYPASS_MIN_DISTINCT:
                        continue
                    # else: admitted via role-FK bypass
                elif (
                    effective_name_sim < low_cardinality_name_sim_bypass
                    or not parent_has_pk_signal
                ):
                    continue
                # else: lexical bypass — strong name + PK signal saves it.

            # ---- A2: asymmetric child-PK downweight ---------------------
            # If both sides are PKs the dominant pattern is "two unrelated id
            # columns happen to share dense ranges".  Drop unless the column
            # names argue for the FK (e.g. orders.id -> orders_archive.id).
            #
            # Tier 1+2 #2 / Sprint A7 #1: the role-FK bypass — when this is
            # a recognised role pointer (self-ref or cross-table) into a PK
            # target, accept even when lexical similarity is low.
            #
            # Both-PK subset uses the tighter threshold
            # (``_A2_BOTH_PK_NAME_SIM_THRESHOLD``) so coincidental same-name
            # PKs across unrelated tables are dropped while inheritance
            # patterns (name_sim = 1.0) still pass.
            if (
                child.is_pk
                and parent.is_pk
                and effective_name_sim <= _A2_BOTH_PK_NAME_SIM_THRESHOLD
                and not role_bypass
            ):
                continue

            seen.add(pair)

            # Tier classifier uses the effective (qualified-or-bare) name
            # similarity so that, e.g., region_id → regions.id is promoted
            # to primary on the qualified match (1.0).
            tier = _classify_tier(
                child=child,
                parent=parent,
                estimated_containment=0.0,
                name_similarity=effective_name_sim,
                parent_pk_unknown=parent_pk_unknown,
            )
            # Role-FK pattern with parent-PK signal is sufficient evidence
            # for primary tier even when both name similarities are low
            # (manager_id → id, posted_by → id, ...).
            if role_bypass and tier == "advisory_lowconf" and not parent_pk_unknown:
                tier = "primary"

            confidence = compute_confidence(
                containment_full=0.0,
                name_similarity=effective_name_sim,
                parent_is_pk=parent.is_pk,
                parent_is_unique_indexed=parent.is_unique_indexed or parent.is_implicit_pk,
                child_distinct=child.distinct_count or 0,
                parent_distinct=parent.distinct_count or 0,
                sketch_jaccard=0.0,
            )

            candidates.append(
                FkCandidate(
                    child_col_id=child.column_id,
                    parent_col_id=parent.column_id,
                    child_table=child.table_name,
                    child_column=child.column_name,
                    parent_table=parent.table_name,
                    parent_column=parent.column_name,
                    estimated_containment=0.0,
                    name_similarity=round(effective_name_sim, 4),
                    type_match=True,
                    source_stage="sql_prefilter",
                    joint_estimate=None,
                    tier=tier,
                    parent_pk_unknown=parent_pk_unknown,
                    confidence=confidence,
                    role_fk_locked=role_bypass and not parent_pk_unknown,
                )
            )

    return candidates


# ---------------------------------------------------------------------------
# Step 4b — FAISS LSH search
# ---------------------------------------------------------------------------


def faiss_lsh_search(
    cols: list[ColSketch],
    lsh_threshold: float = 0.7,
    child_min_distinct_count: int = 100,
    parent_distinct_ratio_min: float = 0.95,
    faiss_vec_bytes: int = 2048,
    top_k: int = 64,
    require_parent_pk: bool = True,
    low_cardinality_name_sim_bypass: float = 0.85,
) -> list[FkCandidate]:
    """FAISS IndexBinaryFlat ANN search + containment re-estimation.

    Like :func:`sql_prefilter`, this admits low-cardinality
    (<``child_min_distinct_count``) columns into the index, then gates
    them per-pair on column-name similarity + parent-PK signal.  This
    matches the pre-filter's "tiny enum lookup" bypass exactly.
    """
    import numpy as np  # noqa: PLC0415
    import faiss  # noqa: PLC0415  # type: ignore[import]

    loaded: list[tuple[ColSketch, object]] = []
    for cs in cols:
        if not cs.sketch_blob:
            continue
        if not cs.is_fk_eligible:
            # PII gating is folded into is_fk_eligible upstream; structural
            # keys (PK / unique / id-named) keep eligibility even when tagged.
            continue
        # NB: the cardinality floor moved to a per-pair check below so the
        # name-similarity bypass can re-admit FKs into tiny enum tables.
        try:
            sk = pickle.loads(cs.sketch_blob)
            loaded.append((cs, sk))
        except (pickle.UnpicklingError, EOFError, AttributeError, ImportError) as exc:
            log.warning(
                "sketch_unpickle_failed",
                column_id=cs.column_id,
                error=type(exc).__name__,
            )
            continue

    if len(loaded) < 2:
        return []

    parent_pk_tables = _parent_pk_table_set(cols) if require_parent_pk else set()

    n = len(loaded)
    bits = faiss_vec_bytes * 8

    matrix = np.zeros((n, faiss_vec_bytes), dtype=np.uint8)
    for i, (_cs, sk) in enumerate(loaded):
        vec = _sketch_to_binary_vector(sk, faiss_vec_bytes)
        matrix[i] = np.frombuffer(vec, dtype=np.uint8)

    index = faiss.IndexBinaryFlat(bits)
    index.add(matrix)

    actual_k = min(top_k + 1, n)
    _distances, indices = index.search(matrix, actual_k)

    seen: set[tuple[int, int]] = set()
    candidates: list[FkCandidate] = []

    for i, (child_cs, child_sk) in enumerate(loaded):
        for rank in range(actual_k):
            j = int(indices[i, rank])
            if j < 0 or j == i:
                continue

            parent_cs, parent_sk = loaded[j]

            # Allow self-referential FKs (different columns in same table);
            # only same column-as-itself is rejected by the j == i check above.
            if not types_compatible(child_cs.type_class, parent_cs.type_class):
                continue

            # ---- A1: parent must be a PK / unique-indexed / implicit-PK ----
            parent_pk_unknown = False
            parent_has_pk_signal = (
                parent_cs.is_pk
                or parent_cs.is_unique_indexed
                or parent_cs.is_implicit_pk
            )
            if require_parent_pk and not parent_has_pk_signal:
                if parent_cs.table_id in parent_pk_tables:
                    continue
                parent_pk_unknown = True

            cd = child_cs.distinct_count
            pd = parent_cs.distinct_count
            if cd is not None and pd is not None and cd > pd * 1.05:
                continue
            # parent must have at least N% as many distincts as child (allows
            # equal-count ONE_TO_ONE candidates).  None ↦ unknown ↦ admit.
            if (
                cd is not None and pd is not None
                and pd < cd * parent_distinct_ratio_min
            ):
                continue

            pair = (child_cs.column_id, parent_cs.column_id)
            if pair in seen:
                continue
            seen.add(pair)

            jaccard = _jaccard_from_sketches(child_sk, parent_sk)
            containment = _containment_from_jaccard(
                jaccard, child_cs.distinct_count, parent_cs.distinct_count
            )
            if containment < lsh_threshold:
                continue

            union_est = int(
                (child_cs.distinct_count + parent_cs.distinct_count)
                / max(1.0 + jaccard, 1e-9)
            )

            name_sim = _name_similarity(child_cs.column_name, parent_cs.column_name)

            # ---- Sprint A7 #1: parent-table-aware name similarity --------
            # Compare the child column against the qualified parent name
            # (``<table>.<column>``) so that ``region_id`` matches
            # ``regions.id`` (≈1.0) instead of just ``id`` (≈0.36).
            qualified_name_sim = _name_similarity(
                child_cs.column_name,
                f"{parent_cs.table_name}.{parent_cs.column_name}",
            )
            effective_name_sim = max(name_sim, qualified_name_sim)

            # Tier 1+2 #2 / Sprint A7 #1: role-suffix bypasses.
            self_ref_role = (
                child_cs.table_id == parent_cs.table_id
                and _is_self_ref_role(
                    child_cs.table_name, child_cs.column_name,
                    parent_cs.table_name, parent_cs.column_name,
                )
            )
            cross_role_fk = _is_role_based_fk(
                child_cs.column_name, parent_cs.table_name, parent_cs.column_name,
            )
            # Generic <x>_id → <table>.id suffix-substring rule (see sql_prefilter).
            suffix_id_match = _is_suffix_id_match(
                child_cs.column_name, parent_cs.table_name, parent_cs.column_name,
            )
            role_bypass = (
                (self_ref_role or cross_role_fk or suffix_id_match)
                and parent_has_pk_signal
            )

            # ---- Tier 1+2 #7: hard reject dense-serial pairs without name match.
            # Role-FK patterns get a free pass.
            if (
                _both_dense_serial(child_cs, parent_cs)
                and effective_name_sim < _DENSE_SERIAL_REJECT_NAMESIM
                and not role_bypass
            ):
                continue

            # ---- Tier 1+2 #9: range overlap as hard signal.
            range_ok = _range_contained(child_cs, parent_cs)
            if range_ok is False:
                continue

            # ---- Cardinality floor with name-similarity bypass ----------
            # Match sql_prefilter behaviour: admit low-cardinality FKs only
            # when both name_sim is high AND parent has PK/unique signal.
            if (child_cs.distinct_count if child_cs.distinct_count is not None else 0) < child_min_distinct_count:
                # Role-FK bypass: same- or cross-table role pointers with a
                # known role suffix and a recognised PK target are valid
                # even at low cardinality.  Floor drops to
                # ``_ROLE_BYPASS_MIN_DISTINCT`` (= 2) for the bypass branch.
                if role_bypass:
                    if (child_cs.distinct_count or 0) < _ROLE_BYPASS_MIN_DISTINCT:
                        continue
                    # else: admitted via role-FK bypass
                elif (
                    effective_name_sim < low_cardinality_name_sim_bypass
                    or not parent_has_pk_signal
                ):
                    continue

            # NB: we deliberately keep the LSH candidate here even if A2
            # would drop it — the strong containment evidence from LSH is a
            # separate signal, and ``_classify_tier`` will demote the noisy
            # id<->id pairs to ``advisory_lowconf``.
            tier = _classify_tier(
                child=child_cs,
                parent=parent_cs,
                estimated_containment=containment,
                name_similarity=effective_name_sim,
                parent_pk_unknown=parent_pk_unknown,
            )
            # Role-FK bypass promotes to primary even with low name_sim,
            # provided the parent has PK signal.
            if role_bypass and tier == "advisory_lowconf" and not parent_pk_unknown:
                tier = "primary"

            confidence = compute_confidence(
                containment_full=containment,
                name_similarity=effective_name_sim,
                parent_is_pk=parent_cs.is_pk,
                parent_is_unique_indexed=parent_cs.is_unique_indexed or parent_cs.is_implicit_pk,
                child_distinct=child_cs.distinct_count or 0,
                parent_distinct=parent_cs.distinct_count or 0,
                sketch_jaccard=float(jaccard),
            )

            candidates.append(
                FkCandidate(
                    child_col_id=child_cs.column_id,
                    parent_col_id=parent_cs.column_id,
                    child_table=child_cs.table_name,
                    child_column=child_cs.column_name,
                    parent_table=parent_cs.table_name,
                    parent_column=parent_cs.column_name,
                    estimated_containment=round(containment, 4),
                    name_similarity=round(effective_name_sim, 4),
                    type_match=True,
                    source_stage="lsh_search",
                    joint_estimate=union_est,
                    tier=tier,
                    parent_pk_unknown=parent_pk_unknown,
                    confidence=confidence,
                    score_features={
                        "sketch_jaccard": round(float(jaccard), 4),
                    },
                    role_fk_locked=role_bypass and not parent_pk_unknown,
                )
            )

    return candidates


# ---------------------------------------------------------------------------
# Convenience wrapper (pure)
# ---------------------------------------------------------------------------


def generate_candidates(
    cols: list[ColSketch],
    lsh_threshold: float = 0.7,
    child_min_distinct_count: int = 100,
    parent_distinct_ratio_min: float = 0.95,
    require_parent_pk: bool = True,
    low_cardinality_name_sim_bypass: float = 0.85,
) -> tuple[list[FkCandidate], list[FkCandidate]]:
    """Run both 4a and 4b, returning (prefilter_candidates, lsh_candidates)."""
    prefilter = sql_prefilter(
        cols,
        parent_distinct_ratio_min=parent_distinct_ratio_min,
        child_min_distinct_count=child_min_distinct_count,
        require_parent_pk=require_parent_pk,
        low_cardinality_name_sim_bypass=low_cardinality_name_sim_bypass,
    )
    lsh = faiss_lsh_search(
        cols,
        lsh_threshold=lsh_threshold,
        child_min_distinct_count=child_min_distinct_count,
        parent_distinct_ratio_min=parent_distinct_ratio_min,
        require_parent_pk=require_parent_pk,
        low_cardinality_name_sim_bypass=low_cardinality_name_sim_bypass,
    )
    return prefilter, lsh


# ---------------------------------------------------------------------------
# Tier 1+2 #6 / #14 — top-K per child + global cap
# ---------------------------------------------------------------------------


def apply_top_k_per_child(
    candidates: list[FkCandidate],
    *,
    top_k: int = 5,
) -> list[FkCandidate]:
    """Group ``primary``-tier candidates by child column, keep only the top-K
    by confidence; demote the rest to ``advisory_lowconf``.

    Sprint A7 #1: candidates flagged ``role_fk_locked=True`` are exempted
    from demotion.  These are recognised role-suffix → PK candidates and
    should always survive ranking — without the exemption they're often
    out-ranked by LSH-noise siblings (random parents whose dense 1..N
    range happens to contain the child's values).

    Mutates the FkCandidate.tier in place AND returns the same list (so the
    caller can chain).  Advisory candidates are passed through unchanged —
    they're already filtered.
    """
    if top_k is None or top_k <= 0:
        return candidates
    # Group primaries by child_col_id.
    groups: dict[int, list[FkCandidate]] = {}
    for c in candidates:
        if c.tier == "primary":
            groups.setdefault(c.child_col_id, []).append(c)
    for _child_id, group in groups.items():
        if len(group) <= top_k:
            continue
        group.sort(key=lambda c: c.confidence, reverse=True)
        # Demote candidates past the top-K cap — but exempt role-FK locked
        # candidates so the role pattern always survives ranking.
        for loser in group[top_k:]:
            if loser.role_fk_locked:
                continue
            loser.tier = "advisory_lowconf"
    return candidates


def filter_bridge_collisions(
    candidates: list[FkCandidate],
    cols_by_id: dict[int, ColSketch],
    table_row_counts: dict[int, int],
    *,
    containment_threshold: float = 0.99,
) -> int:
    """Drop bridge-to-bridge FK collisions.

    Symptom: a single child column ``A.x`` has multiple primary FK
    candidates pointing at different parents (``A.x → B.y``,
    ``A.x → C.y``) where:
      * containment is near-full (>= ``containment_threshold``) on every
        candidate (the value sets are essentially identical);
      * lexical name similarity is similar across all candidates;
    Concretely this is the
    ``inventory.film_id → film_actor.film_id`` class — both ``film`` and
    ``film_actor`` contain ``film_id``, both have full containment, the
    real FK is the smaller / cleaner anchor (``film``).

    Heuristic: when multiple parents have identical near-full
    containment for the same child column, prefer the parent whose row
    count is closest to ``child.distinct_count``.  Demote the rest to
    ``advisory_lowconf`` and tag with ``score_features['bridge_collision'] = 1.0``.

    Returns the number of demoted candidates.  Mutates in place.
    """
    if containment_threshold <= 0:
        return 0

    # Group primaries by child column.  We only act on columns that have
    # >= 2 primary candidates with >= containment_threshold AND identical
    # containment values to within 1e-3 (i.e., the "ambiguous" group).
    by_child: dict[int, list[FkCandidate]] = {}
    for c in candidates:
        if c.tier != "primary":
            continue
        if c.estimated_containment < containment_threshold:
            continue
        by_child.setdefault(c.child_col_id, []).append(c)

    demoted = 0
    for child_col_id, group in by_child.items():
        if len(group) < 2:
            continue

        child = cols_by_id.get(child_col_id)
        if child is None or child.distinct_count is None:
            continue
        target = float(child.distinct_count)

        # Score each parent by row-count distance to child.distinct_count.
        # Smaller distance = better anchor.  A parent without a row count
        # in ``table_row_counts`` falls back to its own
        # ``distinct_count`` (close enough — implicit PKs have
        # distinct == row_count).
        def _row_count(c: FkCandidate) -> Optional[float]:
            parent = cols_by_id.get(c.parent_col_id)
            if parent is None:
                return None
            rc = table_row_counts.get(parent.table_id)
            if rc is not None:
                return float(rc)
            if parent.distinct_count is not None and parent.distinct_count > 0:
                return float(parent.distinct_count)
            return None

        # Score each candidate primarily by NAME SIMILARITY (highest is
        # the canonical anchor) and secondarily by row-count distance to
        # child.distinct.  Without the name-similarity weighting the
        # heuristic picked tiny coincidental lookup tables over the real
        # parent (e.g. `departments.location_id` got demoted to
        # `alert_levels.id` because alert_levels has 22 rows ≈ 21 distinct
        # values, while the real parent `locations` has 50 rows).
        scored: list[tuple[float, float, FkCandidate]] = []
        for c in group:
            rc = _row_count(c)
            row_dist = abs(rc - target) if rc is not None else float("inf")
            # Negate name_similarity so SORT ASC puts highest first.
            scored.append((-(c.name_similarity or 0.0), row_dist, c))

        # Sort by (-name_sim ASC == name_sim DESC), then row-distance ASC,
        # then confidence DESC, then alpha for determinism.
        scored.sort(
            key=lambda t: (
                t[0],
                t[1],
                -t[2].confidence,
                t[2].parent_table,
                t[2].parent_column,
            )
        )

        # The best candidate keeps primary; demote only those whose
        # (name_sim, row_dist) tuple is *strictly worse* than the winner.
        # An exact tie is left alone — both are equally good anchors.
        winner_score = (scored[0][0], scored[0][1])
        scored_iter = [(s[0], s[1], s[2]) for s in scored[1:]]
        # Replace the (dist, c) loop below with our enriched scored_iter.
        # We re-shape so the existing loop body works: synthesize a
        # comparable single value per loser by tagging "worse" with a
        # bool — keeping the winner_score reference for equality check.

        # The best-anchor candidate keeps primary; demote losers whose
        # (name_sim, row_distance) is strictly worse than the winner.
        for neg_name, dist, c in scored_iter:
            if (neg_name, dist) == winner_score:
                # Exact tie — leave alone (both are equally good anchors).
                continue
            if c.role_fk_locked:
                # Role-FK locked candidates (recognised role suffix → PK)
                # are exempt; orthogonal evidence beyond containment.
                continue
            # Self-reference exemption: child and parent in the SAME
            # table is by definition the canonical anchor for that
            # column (e.g. `categories.parent_category_id → categories.id`).
            # Don't let a coincidental cross-table value-set overlap win.
            child = cols_by_id.get(c.child_col_id)
            parent = cols_by_id.get(c.parent_col_id)
            if child is not None and parent is not None and child.table_id == parent.table_id:
                continue
            # Inheritance / IS-A protection: don't demote a candidate whose
            # parent column is a declared PK or implicit PK.  The bridge-
            # collision heuristic was designed for two non-PK columns that
            # happen to share value sets (junction-bridge FPs); declared
            # PKs are real anchors and demoting them was responsible for
            # the adv recall regression on `person.business_entity_id →
            # business_entity.business_entity_id`-shaped FKs.
            if parent is not None and (parent.is_pk or parent.is_implicit_pk or parent.is_unique_indexed):
                continue
            c.tier = "advisory_lowconf"
            c.score_features = dict(c.score_features or {})
            c.score_features["bridge_collision"] = 1.0
            demoted += 1
    return demoted


def apply_range_overlap_penalty(
    candidates: list[FkCandidate],
    cols_by_id: dict[int, ColSketch],
    *,
    distinct_ratio: float = 10.0,
    name_sim_threshold: float = 0.5,
    confidence_penalty: float = 0.10,
) -> int:
    """Apply a range-overlap penalty when child distinct count is much
    smaller than the parent's AND lexical name similarity is weak.

    Catches the ``category.id (1..16) → actor.id (1..400)`` class:
    integer ranges nest by coincidence but the FK is spurious.  When a
    candidate's child column has fewer than
    ``parent.distinct / distinct_ratio`` distinct values AND
    ``name_similarity < name_sim_threshold``:

      * subtract ``confidence_penalty`` (default 0.10) from confidence
      * demote to ``advisory_lowconf``
      * tag ``score_features['range_overlap_penalty'] = 1.0``

    Role-FK locked candidates are exempt — small role tables (few
    managers, few approvers) are legitimate even with low ratios.

    Returns the number of demoted candidates.  Mutates in place.
    """
    if distinct_ratio <= 0:
        return 0
    demoted = 0
    for c in candidates:
        if c.tier != "primary":
            continue
        if c.role_fk_locked:
            continue
        child = cols_by_id.get(c.child_col_id)
        parent = cols_by_id.get(c.parent_col_id)
        if child is None or parent is None:
            continue
        # Self-references (same table) are exempt — `parent_<table>_id → id`
        # has child distinct < parent distinct by design (only some rows
        # have a parent).  Demoting them killed `categories.parent_category_id
        # → categories.id` and `departments.parent_department_id →
        # departments.id` shaped FKs.
        if child.table_id == parent.table_id:
            continue
        cd = child.distinct_count or 0
        pd = parent.distinct_count or 0
        if cd <= 0 or pd <= 0:
            continue
        # Only apply when child range is MUCH smaller than parent's.
        if cd * distinct_ratio > pd:
            continue
        if c.name_similarity >= name_sim_threshold:
            continue
        c.tier = "advisory_lowconf"
        c.confidence = max(0.0, float(c.confidence) - confidence_penalty)
        c.score_features = dict(c.score_features or {})
        c.score_features["range_overlap_penalty"] = 1.0
        demoted += 1
    return demoted


def dedup_bidirectional_candidates(
    candidates: list[FkCandidate],
    cols_by_id: dict[int, ColSketch],
    table_row_counts: Optional[dict[int, int]] = None,
) -> int:
    """Drop bidirectional duplicates left over after ``_reconcile_pk_direction``.

    For each (A.x, B.y) pair where BOTH directions appear in ``candidates``
    we keep exactly one and demote the other to ``advisory_lowconf`` with
    ``tier = 'advisory_lowconf'`` and a ``score_features['reverse_direction']
    = True`` marker so the API can surface it.

    Decision rules (first match wins):
      1. If exactly one side is declared ``is_pk=True``, keep child→parent
         where the parent is the declared PK.
      2. If both sides are declared ``is_pk=True`` (inheritance / IS-A
         pattern), keep the direction whose parent has MORE distinct
         values (the IS-A target — the wider table is the parent).
         Tag the survivor with ``score_features['is_a'] = 1.0``.
      3. If neither is declared ``is_pk`` but both have ``is_implicit_pk``
         from Phase 4 — same rule (more distinct = parent).  This is a
         defence-in-depth: ``_reconcile_pk_direction`` already demotes one
         side so this branch only triggers when reconciliation didn't
         pick a winner.
      4. Tie on every signal — alphabetical (deterministic):
         keep the candidate whose
         ``(parent_table, parent_column, child_table, child_column)``
         tuple sorts first.

    Returns the number of candidates demoted.  Only operates on
    ``tier == 'primary'`` candidates so we don't touch advisory rows.

    Mutates the FkCandidate.tier in place AND records
    ``reverse_direction=True`` on the demoted candidates'
    ``score_features`` map.
    """
    # Group primaries by unordered column-pair.  Same-column (self-loop)
    # candidates never have a reverse partner so we skip them.
    primaries: dict[
        tuple[int, int], list[FkCandidate]
    ] = {}
    for c in candidates:
        if c.tier != "primary":
            continue
        if c.child_col_id == c.parent_col_id:
            continue
        key = (
            min(c.child_col_id, c.parent_col_id),
            max(c.child_col_id, c.parent_col_id),
        )
        primaries.setdefault(key, []).append(c)

    demoted = 0
    for _key, group in primaries.items():
        if len(group) < 2:
            continue
        # Find the two "directions": forward (child < parent col_id) and
        # reverse (child > parent col_id).  More than one entry on a side
        # is rare but possible (LSH + prefilter both emit) — collapse by
        # keeping the highest-confidence one as the representative.
        forward: list[FkCandidate] = []
        reverse: list[FkCandidate] = []
        for c in group:
            if c.child_col_id < c.parent_col_id:
                forward.append(c)
            else:
                reverse.append(c)
        if not forward or not reverse:
            continue  # no bidirectional pair

        # Pick the highest-confidence representative on each side; the
        # other entry on the same side is left alone (it's already the
        # same direction, so dedup doesn't apply to it).
        forward.sort(key=lambda c: c.confidence, reverse=True)
        reverse.sort(key=lambda c: c.confidence, reverse=True)
        f, r = forward[0], reverse[0]

        # Look up sketch metadata via column_id (cols_by_id is the
        # authoritative source — FkCandidate fields are denormalised
        # snapshots).
        f_child = cols_by_id.get(f.child_col_id)
        f_parent = cols_by_id.get(f.parent_col_id)
        r_child = cols_by_id.get(r.child_col_id)
        r_parent = cols_by_id.get(r.parent_col_id)
        if not (f_child and f_parent and r_child and r_parent):
            continue  # safety: missing sketch info, can't reconcile

        # Decision tree.
        winner: Optional[FkCandidate] = None
        is_a: bool = False

        f_parent_pk = bool(f_parent.is_pk)
        r_parent_pk = bool(r_parent.is_pk)
        # Rule 1: exactly-one-side declared PK on the parent.
        if f_parent_pk and not r_parent_pk:
            winner = f
        elif r_parent_pk and not f_parent_pk:
            winner = r

        # Rule 2: both declared is_pk (inheritance pattern).
        if winner is None and f_parent_pk and r_parent_pk:
            is_a = True
            if f_parent.distinct_count > r_parent.distinct_count:
                winner = f
            elif r_parent.distinct_count > f_parent.distinct_count:
                winner = r

        # Rule 3: both implicit PK on parent — same "more distinct = parent"
        # rule.  Falls through if either side has no implicit PK signal.
        if (
            winner is None
            and (f_parent.is_implicit_pk or f_parent.is_unique_indexed)
            and (r_parent.is_implicit_pk or r_parent.is_unique_indexed)
        ):
            if f_parent.distinct_count > r_parent.distinct_count:
                winner = f
            elif r_parent.distinct_count > f_parent.distinct_count:
                winner = r

        # Rule 3b: row-count-vs-distinct disambiguator.  When distinct
        # counts tie (very common for `detail.order_id ↔ header.order_id`),
        # the true parent is the side where row_count == distinct_count
        # (one row per value — the identifier table).  The detail / fact
        # side has row_count > distinct_count.
        if winner is None and table_row_counts:
            def _is_identifier(col: ColSketch) -> Optional[bool]:
                if col.distinct_count is None:
                    return None
                rc = table_row_counts.get(col.table_id)
                if rc is None or rc <= 0:
                    return None
                # Identifier table: row_count ≈ distinct_count (each row's
                # value is unique in the table).  Tolerate 5% slop for
                # HLL noise.
                return abs(rc - col.distinct_count) / max(rc, 1) <= 0.05

            f_parent_id = _is_identifier(f_parent)
            r_parent_id = _is_identifier(r_parent)
            f_child_id = _is_identifier(f_child)
            r_child_id = _is_identifier(r_child)
            # Prefer the direction where parent IS identifier and child is NOT.
            f_score = (1 if f_parent_id else 0) - (1 if f_child_id else 0)
            r_score = (1 if r_parent_id else 0) - (1 if r_child_id else 0)
            if f_score > r_score:
                winner = f
            elif r_score > f_score:
                winner = r

        # Rule 3c: canonical-identifier preference.  When one parent's
        # column is literally `id` and the other parent's is `<x>_id`,
        # the `id` side is the canonical PK; the `<x>_id` is the FK
        # pointer.  Prefer the canonical direction.  Catches
        # `interview_feedback.interview_id → interviews.id` and the
        # general `<a>.<b>_id → <b>s.id` pattern when both pass implicit-
        # PK + identifier checks.
        if winner is None:
            f_par = (f.parent_column or "").lower()
            r_par = (r.parent_column or "").lower()
            f_canon = f_par == "id"
            r_canon = r_par == "id"
            if f_canon and not r_canon:
                winner = f
            elif r_canon and not f_canon:
                winner = r

        # Rule 4: alphabetical tiebreak (deterministic, last resort).
        if winner is None:
            f_key = (f.parent_table, f.parent_column, f.child_table, f.child_column)
            r_key = (r.parent_table, r.parent_column, r.child_table, r.child_column)
            winner = f if f_key <= r_key else r

        loser = r if winner is f else f
        # Tag the survivor with is_a when the inheritance branch fired.
        if is_a:
            winner.score_features = dict(winner.score_features or {})
            winner.score_features["is_a"] = 1.0
        # Demote the loser.  The role-FK lock does NOT exempt from
        # bidirectional dedup — both directions can't be "the right way".
        loser.tier = "advisory_lowconf"
        loser.score_features = dict(loser.score_features or {})
        loser.score_features["reverse_direction"] = 1.0
        demoted += 1

    return demoted


def apply_global_cap(
    candidates: list[FkCandidate],
    *,
    max_relationships: Optional[int] = None,
) -> list[FkCandidate]:
    """Global cap on top of the per-child trim (Tier 1+2 #14).

    When ``max_relationships`` is None or 0, no cap is applied.  Otherwise
    sort the surviving primaries by confidence DESC and demote everything
    past the cap to ``advisory_lowconf``.
    """
    if not max_relationships or max_relationships <= 0:
        return candidates
    primaries = [c for c in candidates if c.tier == "primary"]
    if len(primaries) <= max_relationships:
        return candidates
    primaries.sort(key=lambda c: c.confidence, reverse=True)
    for loser in primaries[max_relationships:]:
        loser.tier = "advisory_lowconf"
    return candidates


# ---------------------------------------------------------------------------
# Phase 4 entry point (orchestrator)
# ---------------------------------------------------------------------------


_PERSIST_BATCH = 500


def _row_counts_from_parquet(
    engine: "Engine",
    schemas: Optional[list[str]] = None,
) -> dict[int, int]:
    """
    Read the exact row count for every extracted table directly from its
    parquet footer.  Cheap (pyarrow reads the footer only, not the data)
    and authoritative — independent of any pg_stats estimate.

    Returns ``{table_id: row_count}`` for tables we can read; tables whose
    parquet is missing or unreadable are silently skipped (the caller
    treats absence as "unknown row count" and skips implicit-PK detection
    for those columns).
    """
    from pathlib import Path
    import pyarrow.parquet as pq  # noqa: PLC0415

    from sqlalchemy import select

    from discovery.results_db import tbl_inventory_t

    out: dict[int, int] = {}
    with engine.connect() as conn:
        stmt = (
            select(tbl_inventory_t.c.table_id, tbl_inventory_t.c.parquet_path)
            .where(tbl_inventory_t.c.parquet_path.is_not(None))
        )
        if schemas:
            stmt = stmt.where(tbl_inventory_t.c.schema_name.in_(list(schemas)))
        rows = conn.execute(stmt).all()

    for table_id, parquet_path in rows:
        if not parquet_path:
            continue
        p = Path(str(parquet_path))
        if not p.exists():
            continue
        try:
            md = pq.ParquetFile(str(p)).metadata
            if md is not None:
                out[int(table_id)] = int(md.num_rows)
        except Exception as exc:
            log.warning(
                "phase4.row_count_read_failed",
                table_id=table_id,
                parquet=str(p),
                error=type(exc).__name__,
            )
    return out


def run_phase_4(engine: "Engine", config: "AppConfig") -> None:
    """
    Orchestrate Phase 4: generate FK candidates.

    Step 4a: SQL pre-filter (in-memory over col_inventory rows)
    Step 4b: FAISS LSH search over serialised sketches
    Persist to fk_candidates via FkCandidate DAO in batched transactions.

    NOTE: this function does NOT call run_log.start/succeed/fail itself; the
    orchestrator wraps the call with the global-scope run_log lifecycle.
    """
    from sqlalchemy import and_, or_, select

    from discovery.results_db import (
        FkCandidate as FkCandidateDAO,
        col_inventory_t,
        pii_findings_t,
        tbl_inventory_t,
        txn,
    )

    schemas_scope = list(getattr(config.source_db, "schemas", None) or [])
    with engine.connect() as conn:
        stmt = (
            select(
                col_inventory_t.c.column_id,
                col_inventory_t.c.table_id,
                tbl_inventory_t.c.table_name,
                col_inventory_t.c.column_name,
                col_inventory_t.c.ordinal_position,
                col_inventory_t.c.type_class,
                col_inventory_t.c.distinct_count,
                col_inventory_t.c.is_fk_eligible,
                col_inventory_t.c.is_pk,
                col_inventory_t.c.is_unique_indexed,
                col_inventory_t.c.is_indexed,
                col_inventory_t.c.min_val,
                col_inventory_t.c.max_val,
                col_inventory_t.c.null_pct,
                col_inventory_t.c.sketch_blob,
            )
            .join(
                tbl_inventory_t,
                tbl_inventory_t.c.table_id == col_inventory_t.c.table_id,
            )
            .where(
                and_(
                    col_inventory_t.c.fingerprinted_at.is_not(None),
                    col_inventory_t.c.sketch_blob.is_not(None),
                    tbl_inventory_t.c.status == "extracted",
                )
            )
        )
        # Schema scope: drop tbl_inventory rows owned by other jobs.
        if schemas_scope:
            stmt = stmt.where(tbl_inventory_t.c.schema_name.in_(schemas_scope))
        rows = conn.execute(stmt).mappings().all()

    raw_rows = list(rows)
    log.info("phase4.sketches_loaded", row_count=len(raw_rows))

    if len(raw_rows) < 2:
        log.info("phase4.skip_too_few_sketches", row_count=len(raw_rows))
        return

    cols: list[ColSketch] = [
        ColSketch(
            column_id=row["column_id"],
            table_id=row["table_id"],
            table_name=row["table_name"],
            column_name=row["column_name"],
            type_class=row["type_class"],
            distinct_count=row["distinct_count"] or 0,
            is_fk_eligible=row["is_fk_eligible"],
            sketch_blob=row["sketch_blob"] or b"",
            is_pk=bool(row["is_pk"]),
            is_unique_indexed=bool(row["is_unique_indexed"]),
            is_indexed=bool(row["is_indexed"]),
            min_val=row["min_val"],
            max_val=row["max_val"],
            null_pct=row["null_pct"],
            ordinal_position=row.get("ordinal_position"),
        )
        for row in raw_rows
    ]

    # ---- Tier 1+2 #4: PII / FK-eligibility cross-check ----------------
    # Mark columns flagged as high-confidence PII so they cannot participate
    # in any FK candidate (either side).  No-op when pii_findings is empty —
    # Phase 3 may not have run yet.
    pii_column_ids: set[int] = set()
    try:
        with engine.connect() as conn:
            pii_rows = conn.execute(
                select(pii_findings_t.c.column_id)
                .where(
                    or_(
                        pii_findings_t.c.validated.is_(True),
                        pii_findings_t.c.score >= 0.7,
                    )
                )
            ).all()
        pii_column_ids = {int(r[0]) for r in pii_rows}
    except Exception as exc:
        # Defensive: pii_findings table may not exist yet on a fresh DB.
        log.info(
            "phase4.pii_findings_unavailable",
            error=type(exc).__name__,
        )

    if pii_column_ids:
        n_marked = 0
        n_kept_structural = 0
        for c in cols:
            if c.column_id in pii_column_ids:
                # A structural identifier (declared PK / unique index, or a
                # column whose name conforms to the universal FK convention
                # ``id`` / ``<x>_id``) keeps its FK eligibility even when the
                # PII detector flagged it.  Two reasons:
                #   1. UUID/text-keyed primary keys often match high-entropy
                #      patterns like ``API_KEY`` — that's a false-positive of
                #      the PII detector, not a reason to skip FK discovery.
                #   2. A genuine PII column (e.g. a phone number used as the
                #      logical row id) can still be the FK key; whether it
                #      also carries privacy implications is reported via
                #      ``pii_findings`` separately.
                cl = c.column_name.lower()
                is_structural_key = (
                    c.is_pk
                    or c.is_unique_indexed
                    or cl == "id"
                    or cl.endswith("_id")
                )
                # is_pii flag stays True for downstream reporting / propagation,
                # but FK-eligibility is preserved for structural keys.
                c.is_pii = True
                if is_structural_key:
                    n_kept_structural += 1
                else:
                    c.is_fk_eligible = False
                    n_marked += 1
        log.info(
            "phase4.pii_columns_excluded",
            columns_marked=n_marked,
            structural_keys_preserved=n_kept_structural,
            findings_total=len(pii_column_ids),
        )

    rel_cfg = getattr(config, "relationships", None)
    lsh_threshold: float = getattr(rel_cfg, "lsh_threshold", 0.7)
    child_min: int = getattr(rel_cfg, "child_min_distinct_count", 100)
    parent_ratio: float = getattr(rel_cfg, "parent_distinct_ratio_min", 0.95)
    require_parent_pk: bool = getattr(rel_cfg, "require_parent_pk", True)
    low_card_bypass: float = getattr(
        rel_cfg, "low_cardinality_name_sim_bypass", 0.85
    )
    top_k_per_child: int = getattr(rel_cfg, "top_k_per_child", 5)
    max_relationships: Optional[int] = getattr(rel_cfg, "max_relationships", None)
    semantic_enabled: bool = getattr(rel_cfg, "semantic_name_similarity", True)

    # ---- Sprint A8 — semantic name similarity warmup -----------------
    # Load the sentence-transformers model once at Phase-4 start so the
    # per-pair calls inside ``best_similarity`` don't pay the load cost
    # on the first miss.  Gracefully no-ops if sentence-transformers
    # isn't installed.
    if semantic_enabled:
        try:
            from discovery import name_similarity as _ns  # noqa: PLC0415
            t0 = _dt.datetime.now()
            _ns.warmup()
            elapsed = (_dt.datetime.now() - t0).total_seconds()
            if getattr(_ns, "SEMANTIC_AVAILABLE", False):
                log.info(
                    "phase4.semantic_name_similarity_loaded",
                    model="sentence-transformers/all-MiniLM-L6-v2",
                    load_seconds=round(elapsed, 2),
                )
            else:
                log.info(
                    "phase4.semantic_name_similarity_unavailable",
                    note="sentence-transformers not installed or model load failed; falling back to lexical similarity",
                )
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "phase4.semantic_name_similarity_warmup_failed",
                error=type(exc).__name__,
            )

    # ---- Implicit-PK detection: derive PK signal from the data itself ----
    # When the source DB does not declare PRIMARY KEY constraints, fall back
    # to inferring PKs from `distinct_count == row_count AND null_pct ~= 0`.
    # Row counts are taken straight from the parquet file metadata (fast,
    # exact — pyarrow only reads the footer).
    table_row_counts = _row_counts_from_parquet(engine, schemas=schemas_scope or None)
    n_implicit = detect_implicit_pks(cols, table_row_counts)
    n_declared = sum(1 for c in cols if c.is_pk or c.is_unique_indexed)
    log.info(
        "phase4.implicit_pk_detected",
        declared_pk_cols=n_declared,
        implicit_pk_cols=n_implicit,
        tables_with_row_counts=len(table_row_counts),
    )

    # ---- Tier 1+2 #5: reverse-direction reconciliation ----------------
    n_demoted = _reconcile_pk_direction(cols)
    log.info(
        "phase4.pk_direction_reconciled",
        demoted=n_demoted,
    )

    log.info(
        "phase4.candidates_starting",
        lsh_threshold=lsh_threshold,
        child_min=child_min,
        require_parent_pk=require_parent_pk,
        low_card_name_sim_bypass=low_card_bypass,
    )

    prefilter_cands, lsh_cands = generate_candidates(
        cols,
        lsh_threshold=lsh_threshold,
        child_min_distinct_count=child_min,
        parent_distinct_ratio_min=parent_ratio,
        require_parent_pk=require_parent_pk,
        low_cardinality_name_sim_bypass=low_card_bypass,
    )

    # Quick stats for the log: how the tier split landed.
    def _tier_counts(cs: list[FkCandidate]) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in cs:
            out[c.tier] = out.get(c.tier, 0) + 1
        return out

    log.info(
        "phase4.candidates_generated",
        prefilter_count=len(prefilter_cands),
        prefilter_tiers=_tier_counts(prefilter_cands),
        lsh_count=len(lsh_cands),
        lsh_tiers=_tier_counts(lsh_cands),
    )

    # ---- Sprint A8 — additive precision passes -----------------------
    # All operate on FkCandidate.tier/score_features in place; recall is
    # never affected (we only demote; never drop).  Order matters:
    #   1. bidirectional dedup — collapse two-direction noise first
    #   2. bridge-collision filter — pick the cleaner anchor when same
    #      child has multiple identical-containment parents
    #   3. range-overlap penalty — soft demote tiny-into-huge with weak
    #      name evidence
    cols_by_id_dedup: dict[int, ColSketch] = {c.column_id: c for c in cols}
    n_reverse_demoted = dedup_bidirectional_candidates(
        prefilter_cands, cols_by_id_dedup, table_row_counts
    ) + dedup_bidirectional_candidates(
        lsh_cands, cols_by_id_dedup, table_row_counts
    )
    n_bridge_demoted = filter_bridge_collisions(
        prefilter_cands, cols_by_id_dedup, table_row_counts
    ) + filter_bridge_collisions(
        lsh_cands, cols_by_id_dedup, table_row_counts
    )
    n_range_demoted = apply_range_overlap_penalty(
        prefilter_cands, cols_by_id_dedup
    ) + apply_range_overlap_penalty(lsh_cands, cols_by_id_dedup)

    log.info(
        "phase4.precision_passes_applied",
        reverse_dir_demoted=n_reverse_demoted,
        bridge_collision_demoted=n_bridge_demoted,
        range_overlap_demoted=n_range_demoted,
    )

    # ---- Tier 1+2 #6: top-K per child column ----
    apply_top_k_per_child(prefilter_cands, top_k=top_k_per_child)
    apply_top_k_per_child(lsh_cands, top_k=top_k_per_child)

    # ---- Tier 1+2 #14: global cap (across both stages) ----
    if max_relationships:
        combined = prefilter_cands + lsh_cands
        apply_global_cap(combined, max_relationships=max_relationships)

    log.info(
        "phase4.candidates_after_ranking",
        prefilter_tiers=_tier_counts(prefilter_cands),
        lsh_tiers=_tier_counts(lsh_cands),
        top_k_per_child=top_k_per_child,
        max_relationships=max_relationships,
    )

    # Persist prefilter first so LSH (re-)writes carry the more accurate
    # estimated_containment / source_stage='lsh_search'.  Batch into
    # transactions of N candidates.
    total = errors = 0

    def _candidate_payload(c: FkCandidate) -> dict[str, Any]:
        return {
            "child_col_id": c.child_col_id,
            "parent_col_id": c.parent_col_id,
            "estimated_containment": c.estimated_containment,
            "name_similarity": c.name_similarity,
            "type_match": c.type_match,
            "source_stage": c.source_stage,
            "joint_estimate": c.joint_estimate,
            "tier": c.tier,
        }

    def _persist_batch(batch: list[FkCandidate]) -> None:
        nonlocal total, errors
        try:
            with txn(engine) as conn:
                dao = FkCandidateDAO(conn)
                for c in batch:
                    dao.upsert(_candidate_payload(c))
            total += len(batch)
        except Exception as exc:
            log.warning(
                "phase4.batch_upsert_failed",
                rows=len(batch),
                error=str(exc),
            )
            errors += len(batch)

    def _persist(cands: list[FkCandidate]) -> None:
        for i in range(0, len(cands), _PERSIST_BATCH):
            _persist_batch(cands[i : i + _PERSIST_BATCH])

    _persist(prefilter_cands)
    _persist(lsh_cands)

    # Sprint A8 — refresh the relationships_unified view so the API can
    # surface composite FKs without separate queries.  Idempotent;
    # silently no-ops if the underlying tables don't exist yet.
    try:
        from discovery.results_db import (  # noqa: PLC0415
            ensure_relationships_unified_view,
        )
        ensure_relationships_unified_view(engine)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "phase4.unified_view_refresh_failed",
            error=type(exc).__name__,
        )

    log.info("phase4.complete", persisted=total, errors=errors)
    if errors > 0 and total == 0:
        raise RuntimeError(
            f"Phase 4: all {errors} candidate upserts failed"
        )
