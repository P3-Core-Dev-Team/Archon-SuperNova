"""
scoring.py — Pure scoring helpers shared by Phase 4 (candidates) and Phase 5
(validate).

Why this lives in its own module
--------------------------------
The multi-feature confidence formula is needed in two places:
  * Phase 4 candidate ranking (used to drive the primary / advisory tier split)
  * Phase 5 final relationship confidence (replaces the old simplistic formula)

Keeping the function in a leaf module avoids a circular dependency between
``candidates`` and ``validate`` and lets unit tests import it without pulling
in DuckDB / FAISS.

The functions here are deliberately pure: no DB, no logging, no IO.  They take
plain values and return plain values, so they're trivial to unit-test.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Dense-serial detector  (Quick win Q3)
# ---------------------------------------------------------------------------


@dataclass
class _SerialFeatures:
    """Subset of a column's stats needed for serial detection."""

    distinct_count: Optional[int]
    null_pct: Optional[float]
    type_class: Optional[str]
    min_val: Any
    max_val: Any


_INT_TYPES = frozenset({"INT_NARROW", "INT_WIDE"})


def _coerce_int(v: Any) -> Optional[int]:
    """Coerce min_val/max_val to int. col_inventory stores them as TEXT."""
    if v is None:
        return None
    if isinstance(v, bool):  # bool is a subclass of int — reject explicitly
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


def is_dense_serial(
    distinct_count: Optional[int],
    null_pct: Optional[float],
    type_class: Optional[str],
    min_val: Any,
    max_val: Any,
) -> bool:
    """
    Return True if a column looks like a 1..N dense surrogate-key serial.

    Definition (mirrors the Q3 spec)::

        min_val == 1
        AND max_val ~= distinct_count       (within 1 to tolerate off-by-one)
        AND null_pct < 0.01
        AND type_class in {INT_NARROW, INT_WIDE}

    Inputs are all the *raw* values from col_inventory; min_val and max_val
    are stored as TEXT in the schema and are coerced to int here.
    """
    if type_class not in _INT_TYPES:
        return False
    if distinct_count is None or distinct_count <= 0:
        return False
    if null_pct is not None and null_pct >= 0.01:
        return False
    lo = _coerce_int(min_val)
    hi = _coerce_int(max_val)
    if lo is None or hi is None:
        return False
    if lo != 1:
        return False
    # max ~= distinct_count: tolerate ±1 to absorb the off-by-one of "did we
    # count zero?".  Slightly more forgiving than strict equality and keeps
    # the test simple.
    if abs(hi - distinct_count) > 1:
        return False
    return True


# ---------------------------------------------------------------------------
# Multi-feature confidence  (M1, Sketch 3 — task A4)
# ---------------------------------------------------------------------------


def _card_ratio_score(child_distinct: int, parent_distinct: int) -> float:
    """
    Cardinality-ratio feature in [0, 1].

      * 1.0 when child_distinct <= parent_distinct  (the FK direction makes
        sense: every child value can be found in the parent, in principle).
      * Decreases linearly when child_distinct exceeds parent_distinct by
        more than 5%; saturates at 0 once child has 2× parent distincts.
    """
    if child_distinct <= 0 or parent_distinct <= 0:
        return 0.0
    if child_distinct <= parent_distinct:
        return 1.0
    over = child_distinct / parent_distinct
    if over <= 1.05:
        return 1.0
    # Linear ramp: ratio 1.05 -> 1.0, ratio 2.0 -> 0.0
    return max(0.0, 1.0 - (over - 1.05) / (2.0 - 1.05))


def compute_confidence(
    *,
    containment_full: float,
    name_similarity: float,
    parent_is_pk: bool,
    parent_is_unique_indexed: bool,
    child_distinct: int,
    parent_distinct: int,
    sketch_jaccard: float = 0.0,
) -> float:
    """
    Multi-feature confidence in [0, 1].

    Weighted combination from the task spec (A4 / Sketch 3 in the plan)::

        0.40 * containment_full
      + 0.30 * name_similarity
      + 0.15 * parent_pk_bonus
                  (1.0 if parent.is_pk
                   else 0.5 if parent.is_unique_indexed
                   else 0.0)
      + 0.10 * card_ratio_score
      + 0.05 * sketch_jaccard

    All inputs are kwargs to keep call sites readable.

    Note on parent_pk_bonus ordering: in Postgres a primary key is also
    unique-indexed.  The ``if/elif`` chain therefore matters — ``is_pk`` is
    checked first so a true PK gets the 1.0 weight, not 0.5.
    """
    cont = max(0.0, min(1.0, float(containment_full)))
    name = max(0.0, min(1.0, float(name_similarity)))
    jacc = max(0.0, min(1.0, float(sketch_jaccard)))

    if parent_is_pk:
        parent_pk_bonus = 1.0
    elif parent_is_unique_indexed:
        parent_pk_bonus = 0.5
    else:
        parent_pk_bonus = 0.0

    card_score = _card_ratio_score(child_distinct, parent_distinct)

    score = (
        0.40 * cont
        + 0.30 * name
        + 0.15 * parent_pk_bonus
        + 0.10 * card_score
        + 0.05 * jacc
    )
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# Name-similarity helpers (used by candidates.py for the lexical gate)
# ---------------------------------------------------------------------------


ROLE_SUFFIXES: frozenset[str] = frozenset({
    "manager_id", "parent_id", "head_id", "owner_id", "supervisor_id",
    "referrer_id", "reports_to", "head_employee_id", "head_user_id",
    "approved_by", "assigned_to", "created_by", "modified_by",
    "updated_by", "managed_by", "posted_by", "reported_by",
    "reviewer_id", "assigned_hr_id", "submitted_by", "received_by",
    "from_id", "to_id", "predecessor_id", "successor_id",
    # Foreign-key role markers — child column is named for the role it plays,
    # not the parent table. Without these, name_similarity('manager_id', 'id')
    # is too low to bypass the cardinality floor for self-refs.
    # Cross-table role markers (HR-domain): *.posted_by → employees.id,
    # *.reviewer_id → employees.id, *.reported_by → employees.id, etc.
})


def _normalise_name(s: str) -> str:
    """Lowercase, drop trailing ``_id`` (or ``.id``), drop trailing ``s``.

    Examples::

        'Customers'  -> 'customer'
        'product_id' -> 'product'
        'IDs'        -> 'id'
        'id'         -> 'id'
        'customers.id' -> 'customer'

    The suffix-stripping is intentionally simple: we don't try to handle
    ``ies`` -> ``y`` or other irregular plurals — over-engineering for our
    use case (column-name lexical similarity).  The order matters: we
    strip the ``_id`` / ``.id`` token first so that ``customers.id``
    becomes ``customers`` -> ``customer`` (rather than ``customers.id``
    losing only its ``s``).  We also support ``.id`` so dotted forms
    written as ``table.id`` normalise the same way as ``table_id``.
    Empty-result protection: we don't strip when the result would be
    empty (so ``'id'`` stays ``'id'`` and ``'s'`` stays ``'s'``).
    """
    out = s.lower()
    # Strip trailing '_id' or '.id', but not if it would empty the string.
    for suf in ("_id", ".id"):
        if out.endswith(suf) and len(out) > len(suf):
            out = out[: -len(suf)]
            break
    # Strip a single trailing 's' as a simple plural (no 'ies' -> 'y').
    if out.endswith("s") and len(out) > 1:
        out = out[:-1]
    return out


def name_similarity(a: str, b: str, *, plural_normalize: bool = True) -> float:
    """SequenceMatcher ratio with optional plural / suffix normalisation.

    With ``plural_normalize=True`` (default), both inputs are passed
    through :func:`_normalise_name` before comparison so that

        ``'customer_id'``  vs ``'customers.id'``  -> 1.0
        ``'orders'``        vs ``'order'``         -> 1.0
        ``'shipping_address_id'`` vs ``'addresses.id'`` -> ~0.85

    We return ``max(normalised_ratio, raw_ratio)`` so normalisation can
    only ever *increase* similarity vs. the unnormalised comparison.

    With ``plural_normalize=False`` we fall back to a plain
    SequenceMatcher ratio over the raw (lowercased) strings.
    """
    raw_ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    if not plural_normalize:
        return raw_ratio
    norm_ratio = SequenceMatcher(
        None, _normalise_name(a), _normalise_name(b)
    ).ratio()
    return max(raw_ratio, norm_ratio)


def is_self_ref_role(
    child_table: str,
    child_column: str,
    parent_table: str,
    parent_column: str,
) -> bool:
    """Return True iff this looks like a self-referential role FK.

    Conditions (all required):
      * ``child_table`` and ``parent_table`` refer to the same table
        (case-insensitive).
      * ``child_column`` (case-insensitive) is one of the known role
        suffixes in :data:`ROLE_SUFFIXES` — ``manager_id``, ``parent_id``,
        etc.
      * ``parent_column`` is the table's primary-key column —
        ``id`` or ``<plural-norm-of-parent_table>_id``
        (e.g. ``employee_id`` for parent_table ``employees``).

    Used by ``candidates.py`` to bypass the lexical-name-similarity gate
    for self-referential FKs, where the column name encodes the role
    (e.g. ``manager_id``) rather than the parent table.
    """
    if child_table.lower() != parent_table.lower():
        return False
    if child_column.lower() not in ROLE_SUFFIXES:
        return False
    parent_col_lc = parent_column.lower()
    pk_col_candidate = f"{_normalise_name(parent_table)}_id"
    return parent_col_lc in {"id", pk_col_candidate}


def is_suffix_id_match(
    child_column: str,
    parent_table: str,
    parent_column: str,
) -> bool:
    """Generic ``<x>_id`` → ``<table>.id`` substring match.

    Returns True iff:
      * ``child_column`` ends with ``_id`` (case-insensitive); AND
      * ``parent_column`` is the parent table's PK — ``id`` or
        ``<plural-norm-of-parent_table>_id``; AND
      * the prefix of the child column (everything before ``_id``) is
        a substring of ``_normalise_name(parent_table)``, OR vice versa.

    Catches the universal Postgres convention ``foo_id → foos.id`` for any
    naming scheme — including ``storage_policy_id → ads_st_config_policy.id``
    where lexical SequenceMatcher similarity is too low to clear the 0.85
    bypass gate but the suffix convention itself is unambiguous evidence.

    Used by ``candidates.py`` as a third role-FK admission rule alongside
    :func:`is_self_ref_role` and :func:`is_role_based_fk` so candidates
    of this shape skip the cardinality floor when the parent has a PK
    signal — the standard "let Phase 5 data-containment be the arbiter"
    pattern.
    """
    cc = child_column.lower()
    if not cc.endswith("_id") or len(cc) <= 3:
        return False
    pc = parent_column.lower()
    parent_norm = _normalise_name(parent_table)
    if pc not in {"id", f"{parent_norm}_id"}:
        return False
    prefix = cc[:-3]                    # strip "_id"
    if not prefix or prefix == "id":
        return False

    # Token-overlap test (generic across all naming conventions).  Split
    # both sides on ``_`` and singularize, then require ANY token pair
    # ≥3 chars where one token is a substring of the other.  This catches:
    #   storage_policy_id → ads_st_config_policy.id   (shared "policy")
    #   group_id          → ads_user_group.id         (shared "group")
    #   application_id    → ads_app.id                ("app" ⊂ "application")
    #   user_id           → users.id                  (after singularization)
    # The data-containment check in Phase 5 is the real precision gate;
    # this rule's job is purely to pass the candidate through to Phase 5.
    def _toks(s: str) -> list[str]:
        out = []
        for tok in s.split("_"):
            t = tok.lower()
            if t.endswith("s") and len(t) > 1 and not t.endswith("ss"):
                t = t[:-1]
            if len(t) >= 3:
                out.append(t)
        return out

    p_tokens = _toks(prefix)
    t_tokens = _toks(parent_norm)
    if not p_tokens or not t_tokens:
        # Fall back to the broader substring check on the full strings.
        return prefix in parent_norm or parent_norm in prefix
    for a in p_tokens:
        for b in t_tokens:
            if a == b or a in b or b in a:
                return True
    return False


def is_role_based_fk(
    child_column: str,
    parent_table: str,
    parent_column: str,
) -> bool:
    """Return True iff ``child_column`` is a known role-suffix AND
    ``parent_column`` is the PK of ``parent_table``.

    Conditions (all required):
      * ``child_column`` (case-insensitive) is one of :data:`ROLE_SUFFIXES`
        (e.g. ``posted_by``, ``referrer_id``, ``approved_by``).
      * ``parent_column`` is the parent table's primary key — either
        ``id`` or ``<plural-norm-of-parent_table>_id``.

    This is the CROSS-TABLE counterpart of :func:`is_self_ref_role`.
    Independent of whether ``child_table == parent_table``: role-suffix
    columns frequently reference an unrelated parent table by role
    (``candidates.referrer_id -> employees.id``,
    ``job_postings.posted_by -> employees.id``,
    ``incidents.reported_by -> employees.id``, etc.).

    Used by ``candidates.py`` to bypass the lexical name-similarity gate
    when a role-suffix child column is referencing a recognised PK target.
    Without this bypass the lexical gate rejects these candidates because
    ``name_similarity('posted_by', 'id') ≈ 0.18`` — well under the 0.85
    low-cardinality bypass threshold even though the relationship is real.
    """
    if child_column.lower() not in ROLE_SUFFIXES:
        return False
    parent_table_norm = _normalise_name(parent_table)
    pc = parent_column.lower()
    return pc == "id" or pc == f"{parent_table_norm}_id"
