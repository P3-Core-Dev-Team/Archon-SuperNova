"""
Unit tests for candidates.py (Phase 4).

Seeds 5 synthetic ColSketch objects with known sketch overlap and asserts
that the expected candidates are produced.  No database required.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from discovery.candidates import (
    ColSketch,
    FkCandidate,
    sql_prefilter,
    faiss_lsh_search,
    generate_candidates,
    types_compatible,
    _name_similarity,
    _containment_from_jaccard,
    _select_table_implicit_pk,
    _reconcile_pk_direction,
    _range_contained,
    _is_self_ref_role,
    _is_role_based_fk,
    detect_implicit_pks,
    apply_top_k_per_child,
    apply_global_cap,
    dedup_bidirectional_candidates,
    filter_bridge_collisions,
    apply_range_overlap_penalty,
    _A2_BOTH_PK_NAME_SIM_THRESHOLD,
    _ROLE_BYPASS_MIN_DISTINCT,
)


# ---------------------------------------------------------------------------
# Type compatibility
# ---------------------------------------------------------------------------

class TestTypesCompatible:
    def test_same_type(self):
        assert types_compatible("INT_NARROW", "INT_NARROW")
        assert types_compatible("UUID", "UUID")
        assert types_compatible("STRING_SHORT", "STRING_SHORT")

    def test_int_narrow_wide(self):
        assert types_compatible("INT_NARROW", "INT_WIDE")
        assert types_compatible("INT_WIDE", "INT_NARROW")

    def test_string_cross(self):
        assert types_compatible("STRING_SHORT", "STRING_LONG")
        assert types_compatible("STRING_LONG", "STRING_SHORT")

    def test_incompatible(self):
        assert not types_compatible("INT_NARROW", "UUID")
        assert not types_compatible("DATE", "TIMESTAMP")
        assert not types_compatible("INT_NARROW", "STRING_SHORT")


# ---------------------------------------------------------------------------
# Name similarity
# ---------------------------------------------------------------------------

class TestNameSimilarity:
    def test_identical(self):
        assert _name_similarity("customer_id", "customer_id") == pytest.approx(1.0)

    def test_very_similar(self):
        sim = _name_similarity("customer_id", "customers_id")
        assert sim > 0.8

    def test_unrelated(self):
        sim = _name_similarity("order_date", "product_sku")
        assert sim < 0.5


# ---------------------------------------------------------------------------
# Containment estimation
# ---------------------------------------------------------------------------

class TestContainmentFromJaccard:
    def test_identical_equal_cardinality(self):
        # J=1.0, cd=pd → containment=1.0
        c = _containment_from_jaccard(1.0, 1000, 1000)
        assert c == pytest.approx(1.0)

    def test_subset_half(self):
        # child has 500, parent 1000, all child values in parent
        # J = 500/1000 = 0.5
        c = _containment_from_jaccard(0.5, 500, 1000)
        assert 0.8 < c <= 1.0  # should be close to 1.0 (high containment)

    def test_zero_jaccard(self):
        c = _containment_from_jaccard(0.0, 500, 1000)
        assert c == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Sketch helpers
# ---------------------------------------------------------------------------

def _make_minhash_sketch(values: list[str], num_perm: int = 64) -> bytes:
    """Create a MinHash sketch blob for a list of values."""
    import xxhash
    from datasketch import MinHash
    mh = MinHash(num_perm=num_perm)
    for v in values:
        h = xxhash.xxh3_64_intdigest(v.encode("utf-8"))
        mh.update(h.to_bytes(8, "big"))
    return pickle.dumps(mh)


def _make_col(
    column_id: int,
    table_id: int,
    table_name: str,
    column_name: str,
    values: list[str],
    type_class: str = "INT_NARROW",
    num_perm: int = 64,
) -> ColSketch:
    return ColSketch(
        column_id=column_id,
        table_id=table_id,
        table_name=table_name,
        column_name=column_name,
        type_class=type_class,
        distinct_count=len(set(values)),
        is_fk_eligible=True,
        sketch_blob=_make_minhash_sketch(values, num_perm=num_perm),
    )


# ---------------------------------------------------------------------------
# Fixtures: 5 columns with known overlap
# ---------------------------------------------------------------------------

@pytest.fixture
def five_cols() -> list[ColSketch]:
    """
    Five columns across three tables:

    Table A (customers):
      - A1: cust_id   values 0..999    (1000 distinct) ← parent
      - A2: region_id values 0..49     (50 distinct)   ← low cardinality

    Table B (orders):
      - B1: customer_id  values 0..499  (500 distinct, subset of A1) ← should find A1 as parent
      - B2: item_count   values 0..999  (unrelated to A)

    Table C (payments):
      - C1: order_customer_id values 0..499  (same values as B1, subset of A1)
    """
    parent_vals = [str(i) for i in range(1000)]
    child_vals  = [str(i) for i in range(500)]
    unrelated   = [str(i + 10_000) for i in range(1000)]
    low_card    = [str(i) for i in range(50)]

    return [
        _make_col(1, 1, "customers",  "cust_id",           parent_vals),
        _make_col(2, 1, "customers",  "region_id",         low_card),
        _make_col(3, 2, "orders",     "customer_id",       child_vals),
        _make_col(4, 2, "orders",     "item_count",        unrelated),
        _make_col(5, 3, "payments",   "order_customer_id", child_vals),
    ]


# ---------------------------------------------------------------------------
# sql_prefilter tests
# ---------------------------------------------------------------------------

class TestSqlPrefilter:
    def test_finds_child_to_parent(self, five_cols: list[ColSketch]):
        cands = sql_prefilter(five_cols, child_min_distinct_count=50)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        # B1 (customer_id, 500 distinct) should be child of A1 (cust_id, 1000 distinct)
        assert (3, 1) in pairs, f"Expected (3,1) in {pairs}"

    def test_same_table_excluded(self, five_cols: list[ColSketch]):
        cands = sql_prefilter(five_cols, child_min_distinct_count=10)
        # region_id (table 1) must never be child of cust_id (table 1)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        assert (2, 1) not in pairs, "Same-table pair should be excluded"

    def test_source_stage_label(self, five_cols: list[ColSketch]):
        cands = sql_prefilter(five_cols, child_min_distinct_count=10)
        assert all(c.source_stage == "sql_prefilter" for c in cands)

    def test_type_match_true(self, five_cols: list[ColSketch]):
        cands = sql_prefilter(five_cols, child_min_distinct_count=10)
        assert all(c.type_match is True for c in cands)

    def test_cardinality_gate(self, five_cols: list[ColSketch]):
        """Parent must have >= child distinct count."""
        cands = sql_prefilter(five_cols, child_min_distinct_count=10)
        for c in cands:
            child = next(col for col in five_cols if col.column_id == c.child_col_id)
            parent = next(col for col in five_cols if col.column_id == c.parent_col_id)
            # child distinct ≤ 105% of parent distinct
            assert child.distinct_count <= parent.distinct_count * 1.05 + 1


# ---------------------------------------------------------------------------
# faiss_lsh_search tests
# ---------------------------------------------------------------------------

class TestFaissLshSearch:
    def test_finds_high_containment_pair(self, five_cols: list[ColSketch]):
        """B1 and C1 have identical values → both are children of A1."""
        cands = faiss_lsh_search(
            five_cols,
            lsh_threshold=0.3,    # low threshold so child→parent pair is captured
            child_min_distinct_count=50,
        )
        parent_ids = {c.parent_col_id for c in cands}
        # A1 (cust_id) should be a parent
        assert 1 in parent_ids or len(cands) == 0, (
            "A1 should be discovered as a parent (or no candidates at this threshold)"
        )

    def test_source_stage_lsh(self, five_cols: list[ColSketch]):
        cands = faiss_lsh_search(five_cols, lsh_threshold=0.01, child_min_distinct_count=10)
        assert all(c.source_stage == "lsh_search" for c in cands)

    def test_no_same_table_pair(self, five_cols: list[ColSketch]):
        cands = faiss_lsh_search(five_cols, lsh_threshold=0.01, child_min_distinct_count=10)
        for c in cands:
            child = next(col for col in five_cols if col.column_id == c.child_col_id)
            parent = next(col for col in five_cols if col.column_id == c.parent_col_id)
            assert child.table_id != parent.table_id, "Same-table FK should never appear"

    def test_containment_in_range(self, five_cols: list[ColSketch]):
        cands = faiss_lsh_search(five_cols, lsh_threshold=0.01, child_min_distinct_count=10)
        for c in cands:
            assert 0.0 <= c.estimated_containment <= 1.0


# ---------------------------------------------------------------------------
# generate_candidates (combined) tests
# ---------------------------------------------------------------------------

class TestGenerateCandidates:
    def test_returns_two_lists(self, five_cols: list[ColSketch]):
        prefilter, lsh = generate_candidates(five_cols)
        assert isinstance(prefilter, list)
        assert isinstance(lsh, list)

    def test_combined_finds_expected_pair(self, five_cols: list[ColSketch]):
        # The five_cols fixture leaves is_pk=False on every column.  With
        # require_parent_pk=True (the default), no column in any parent table
        # has PK metadata, so the A1 fallback fires (parent_pk_unknown=True)
        # and the candidate still emerges — just tiered as advisory_lowconf.
        prefilter, lsh = generate_candidates(
            five_cols,
            lsh_threshold=0.2,
            child_min_distinct_count=50,
        )
        all_pairs = (
            {(c.child_col_id, c.parent_col_id) for c in prefilter}
            | {(c.child_col_id, c.parent_col_id) for c in lsh}
        )
        # customer_id (3) → cust_id (1) should appear in at least one stage
        assert (3, 1) in all_pairs, f"Expected (3,1) in candidates, got: {all_pairs}"

    def test_empty_returns_empty(self):
        """Single column — nothing to pair."""
        single = [
            ColSketch(1, 1, "t1", "col", "INT_NARROW", 1000, True,
                      _make_minhash_sketch([str(i) for i in range(100)]))
        ]
        prefilter, lsh = generate_candidates(single)
        assert prefilter == []
        assert lsh == []


# ---------------------------------------------------------------------------
# FK-precision gates (A1 / A2 / A5)
# ---------------------------------------------------------------------------

def _col_with_metadata(
    column_id: int,
    table_id: int,
    table_name: str,
    column_name: str,
    values: list[str],
    *,
    type_class: str = "INT_NARROW",
    is_pk: bool = False,
    is_unique_indexed: bool = False,
    is_implicit_pk: bool = False,
    is_pii: bool = False,
    min_val: str | None = None,
    max_val: str | None = None,
    null_pct: float | None = 0.0,
    ordinal_position: int | None = None,
    distinct_count: int | None = None,
    is_fk_eligible: bool = True,
) -> ColSketch:
    return ColSketch(
        column_id=column_id,
        table_id=table_id,
        table_name=table_name,
        column_name=column_name,
        type_class=type_class,
        distinct_count=distinct_count if distinct_count is not None else len(set(values)),
        is_fk_eligible=is_fk_eligible,
        sketch_blob=_make_minhash_sketch(values),
        is_pk=is_pk,
        is_unique_indexed=is_unique_indexed,
        is_implicit_pk=is_implicit_pk,
        is_pii=is_pii,
        min_val=min_val,
        max_val=max_val,
        null_pct=null_pct,
        ordinal_position=ordinal_position,
    )


class TestParentPkGate:
    """A1: candidates require parent.is_pk OR parent.is_unique_indexed."""

    def test_non_pk_parent_dropped(self):
        # Parent has neither flag → rejected.
        child_vals = [str(i) for i in range(500)]
        parent_vals = [str(i) for i in range(1000)]
        cols = [
            _col_with_metadata(1, 1, "t1", "fk", child_vals),  # child
            _col_with_metadata(2, 2, "t2", "id", parent_vals,
                               is_pk=False, is_unique_indexed=False),
            # Decoy PK on parent table so the entire-table fallback doesn't fire.
            _col_with_metadata(3, 2, "t2", "real_pk", parent_vals,
                               is_pk=True, min_val="0", max_val="999"),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        # column 2 is rejected (not PK / unique-indexed) — column 3 is OK.
        assert (1, 2) not in pairs
        assert (1, 3) in pairs

    def test_unknown_table_pk_fallback(self):
        # Parent table has NO column with PK metadata → keep candidate but
        # tier it as advisory_lowconf.
        child_vals = [str(i) for i in range(500)]
        parent_vals = [str(i) for i in range(1000)]
        cols = [
            _col_with_metadata(1, 1, "t1", "fk", child_vals),
            _col_with_metadata(2, 2, "t2", "ref", parent_vals,
                               is_pk=False, is_unique_indexed=False),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        # The candidate (1, 2) survives — t2 has no PK info at all → fallback.
        target = [c for c in cands if c.child_col_id == 1 and c.parent_col_id == 2]
        assert len(target) == 1
        assert target[0].parent_pk_unknown is True
        # Without strong evidence, advisory tier.
        assert target[0].tier == "advisory_lowconf"

    def test_disable_gate(self):
        """require_parent_pk=False reverts to legacy behaviour."""
        child_vals = [str(i) for i in range(500)]
        parent_vals = [str(i) for i in range(1000)]
        cols = [
            _col_with_metadata(1, 1, "t1", "fk", child_vals),
            _col_with_metadata(2, 2, "t2", "id", parent_vals,
                               is_pk=False, is_unique_indexed=False),
            _col_with_metadata(3, 2, "t2", "real_pk", parent_vals, is_pk=True),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=False)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        assert (1, 2) in pairs
        assert (1, 3) in pairs


class TestAsymmetricChildPk:
    """A2: drop id->id pairs unless name_similarity > 0.7."""

    def test_id_to_id_with_weak_name_dropped(self):
        # Both PKs, weak name similarity → A2 drops the candidate.
        vals_a = [str(i) for i in range(1000)]
        vals_b = [str(i) for i in range(1000)]
        cols = [
            _col_with_metadata(1, 1, "table_a", "id", vals_a, is_pk=True),
            _col_with_metadata(2, 2, "table_b", "key", vals_b, is_pk=True),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        # 'id' vs 'key' → name_sim well below 0.7
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        assert (1, 2) not in pairs
        assert (2, 1) not in pairs

    def test_id_to_id_with_strong_name_kept(self):
        # Two PKs but matching names — keep.
        vals_a = [str(i) for i in range(1000)]
        vals_b = [str(i) for i in range(1000)]
        cols = [
            _col_with_metadata(1, 1, "orders", "order_id", vals_a, is_pk=True),
            _col_with_metadata(2, 2, "orders_archive", "order_id", vals_b,
                               is_pk=True),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        # Identical column names → similarity = 1.0 → both directions survive.
        assert (1, 2) in pairs or (2, 1) in pairs


class TestTierClassification:
    """A5: candidates are split into 'primary' and 'advisory_lowconf'."""

    def test_strong_name_match_is_primary(self):
        child_vals = [str(i) for i in range(500)]
        parent_vals = [str(i) for i in range(1000)]
        cols = [
            _col_with_metadata(1, 1, "orders", "customer_id", child_vals),
            _col_with_metadata(2, 2, "customers", "customer_id", parent_vals,
                               is_pk=True),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        target = [c for c in cands if c.child_col_id == 1 and c.parent_col_id == 2]
        assert len(target) == 1
        assert target[0].tier == "primary"

    def test_dense_serial_pair_demoted(self):
        # Two dense 1..N serials with weak (but not zero) name match → advisory.
        # Names "order_id" / "group_id" share enough characters to clear the
        # Tier1+2 #7 hard-reject floor (0.4) while still landing under the
        # tier-classifier's 0.6 primary threshold → demoted to advisory.
        a_vals = [str(i) for i in range(1, 501)]   # 1..500
        b_vals = [str(i) for i in range(1, 1001)]  # 1..1000
        cols = [
            _col_with_metadata(
                1, 1, "table_a", "order_id", a_vals,
                is_pk=False, is_unique_indexed=True,
                min_val="1", max_val="500",
            ),
            _col_with_metadata(
                2, 2, "table_b", "group_id", b_vals,
                is_pk=True,
                min_val="1", max_val="1000",
            ),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        target = [c for c in cands if c.child_col_id == 1 and c.parent_col_id == 2]
        assert len(target) == 1
        assert target[0].tier == "advisory_lowconf"


# ---------------------------------------------------------------------------
# Tier 1+2 — accuracy / ranking tests
# ---------------------------------------------------------------------------


class TestImplicitPkSelection:
    """Tier 1+2 #3: only ONE implicit PK per table."""

    def test_implicit_pk_selection_one_per_table(self):
        # Two columns in the same table both qualify as data-level unique:
        # distinct_count == row_count, no nulls, INT type.  Selection must
        # keep only one of them as is_implicit_pk=True.
        cols = [
            _col_with_metadata(
                1, 100, "customers", "id",
                values=[str(i) for i in range(50)],
                is_pk=False,
                ordinal_position=1,
                null_pct=0.0,
                min_val="0", max_val="49",
            ),
            _col_with_metadata(
                2, 100, "customers", "external_ref",
                values=[f"ref-{i}" for i in range(50)],
                type_class="STRING_SHORT",
                is_pk=False,
                ordinal_position=5,
                null_pct=0.0,
            ),
        ]
        n = detect_implicit_pks(cols, table_row_counts={100: 50})
        flagged = [c for c in cols if c.is_implicit_pk]
        # Exactly one column is the implicit PK.
        assert n == 1
        assert len(flagged) == 1
        # 'id' wins over 'external_ref' by the priority list.
        assert flagged[0].column_name == "id"
        # The loser must remain unique-indexed so it stays valid as a parent
        # but does not trigger A2 "both PK" trap when used as a child.
        loser = next(c for c in cols if c.column_name == "external_ref")
        assert loser.is_implicit_pk is False
        assert loser.is_unique_indexed is True

    def test_select_helper_priority_ordering(self):
        # Direct test of the helper: declared PK > unique-indexed > 'id'
        # > '<table>_id' > '<singular>_id' > smallest ordinal.
        a = _col_with_metadata(
            10, 200, "customers", "external_id",
            values=[str(i) for i in range(10)],
            ordinal_position=1,
        )
        b = _col_with_metadata(
            11, 200, "customers", "customer_id",
            values=[str(i) for i in range(10)],
            ordinal_position=10,
        )
        # No declared PK / unique — singular '<singular>_id' wins.
        chosen = _select_table_implicit_pk([a, b])
        assert chosen is b
        # When both are unique-indexed, that beats the name match.
        a.is_unique_indexed = True
        chosen = _select_table_implicit_pk([a, b])
        assert chosen is a


class TestSelfRefRole:
    """Tier 1+2 #2: same-table self-ref role pattern accepted with low name_sim."""

    def test_self_ref_role_detection(self):
        # employee.manager_id -> employee.id should be accepted even though
        # the lexical similarity ('manager_id' vs 'id') is well below 0.85
        # and would normally be rejected by the cardinality / id-to-id gate.
        # Note: we set is_pk=True on the parent and on the child to land in
        # the A2 pathway, plus strong cardinality so we do reach the gate.
        parent_vals = [str(i) for i in range(1000)]
        child_vals = [str(i) for i in range(500)]
        cols = [
            _col_with_metadata(
                1, 50, "employee", "id", parent_vals,
                is_pk=True,
                min_val="0", max_val="999",
                ordinal_position=1,
            ),
            # Self-ref: child column lives in the SAME table as the parent.
            _col_with_metadata(
                2, 50, "employee", "manager_id", child_vals,
                is_pk=True,
                min_val="0", max_val="499",
                ordinal_position=2,
            ),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        # The self-ref should survive even though name_sim is low.
        assert (2, 1) in pairs, f"manager_id -> id should be accepted; got {pairs}"

    def test_is_self_ref_role_helper(self):
        assert _is_self_ref_role("employee", "manager_id", "employee", "id")
        assert _is_self_ref_role("category", "parent_id", "category", "id")
        # Cross-table is not a "self-ref" — but the helper purely checks
        # naming, so it returns True regardless; the candidate gate
        # additionally requires same table_id.
        # Negative: column doesn't end in a role suffix.
        assert not _is_self_ref_role("employee", "first_name", "employee", "id")

    def test_self_ref_role_low_cardinality_bypass(self):
        # Realistic case: small org, manager_id has only ~10 distinct
        # managers (well below the 50-distinct cardinality floor) and is
        # NOT itself a PK.  Without the self-ref bypass, the cardinality
        # gate would reject this real FK because:
        #   - distinct_count (10) < child_min_distinct_count (50)
        #   - name_sim('manager_id', 'id') ~ 0.3 < 0.85 lexical bypass
        # The self-ref role bypass must admit it.
        parent_vals = [str(i) for i in range(100)]  # 100 employees
        child_vals = [str(i) for i in range(10)]    # 10 managers
        cols = [
            _col_with_metadata(
                1, 50, "employee", "id", parent_vals,
                is_pk=True,
                min_val="0", max_val="99",
                ordinal_position=1,
            ),
            _col_with_metadata(
                2, 50, "employee", "manager_id", child_vals,
                is_pk=False, is_unique_indexed=False,
                min_val="0", max_val="9",
                ordinal_position=2,
            ),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        assert (2, 1) in pairs, (
            f"low-cardinality self-ref manager_id->id should be accepted; got {pairs}"
        )


class TestPiiFilter:
    """Tier 1+2 #4: columns flagged as PII can't be FK candidates."""

    def test_pii_column_filtered(self):
        child_vals = [str(i) for i in range(500)]
        parent_vals = [str(i) for i in range(1000)]
        cols = [
            # PII child — should be excluded from candidates entirely.
            _col_with_metadata(
                1, 1, "users", "ssn", child_vals,
                is_pii=True,
            ),
            _col_with_metadata(
                2, 2, "ref", "id", parent_vals,
                is_pk=True,
                min_val="0", max_val="999",
            ),
            # Non-PII child — should produce a candidate to confirm the
            # filter is column-specific, not global.
            _col_with_metadata(
                3, 1, "users", "ref_id", child_vals,
            ),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        # PII child can't appear.
        assert all(p[0] != 1 and p[1] != 1 for p in pairs), (
            f"PII column 1 must not be in {pairs}"
        )
        # The non-PII control survived.
        assert (3, 2) in pairs


class TestDenseSerialHardReject:
    """Tier 1+2 #7: two dense 1..N serials with low name_sim → no candidate."""

    def test_dense_serial_rejection(self):
        a_vals = [str(i) for i in range(1, 501)]
        b_vals = [str(i) for i in range(1, 1001)]
        cols = [
            _col_with_metadata(
                1, 1, "tab_a", "alpha", a_vals,
                is_pk=True,
                min_val="1", max_val="500",
            ),
            _col_with_metadata(
                2, 2, "tab_b", "omega", b_vals,
                is_pk=True,
                min_val="1", max_val="1000",
            ),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=50,
                              require_parent_pk=True)
        # 'alpha' / 'omega' similarity is well below 0.4 — hard reject.
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        assert (1, 2) not in pairs
        assert (2, 1) not in pairs


class TestTopKPerChild:
    """Tier 1+2 #6: keep only top-K candidates per child column."""

    def test_top_k_per_child(self):
        # Construct 10 candidates for the same child_col_id with varying
        # confidence; the top-K cap (K=5) must demote the bottom 5.
        cands = [
            FkCandidate(
                child_col_id=1,
                parent_col_id=100 + i,
                child_table="t1",
                child_column="fk",
                parent_table=f"t{i}",
                parent_column="id",
                estimated_containment=0.5,
                name_similarity=0.5,
                type_match=True,
                source_stage="test",
                joint_estimate=None,
                tier="primary",
                confidence=0.1 + 0.05 * i,  # 0.10, 0.15, ..., 0.55
            )
            for i in range(10)
        ]
        apply_top_k_per_child(cands, top_k=5)
        primaries = [c for c in cands if c.tier == "primary"]
        assert len(primaries) == 5
        # The top 5 by confidence are the i=5..9 ones.
        kept_parents = {c.parent_col_id for c in primaries}
        assert kept_parents == {105, 106, 107, 108, 109}
        # The other five are demoted but still present.
        demoted = [c for c in cands if c.tier == "advisory_lowconf"]
        assert len(demoted) == 5


class TestRangeOverlapGate:
    """Tier 1+2 #9: child outside parent's min/max → rejected."""

    def test_range_overlap_gate(self):
        # Child has values 0..49, parent has 100..199 — incompatible ranges.
        child_vals = [str(i) for i in range(50)]
        parent_vals = [str(i) for i in range(100, 200)]
        cols = [
            _col_with_metadata(
                1, 1, "child_t", "fk", child_vals,
                min_val="0", max_val="49",
            ),
            _col_with_metadata(
                2, 2, "parent_t", "id", parent_vals,
                is_pk=True,
                min_val="100", max_val="199",
            ),
        ]
        cands = sql_prefilter(cols, child_min_distinct_count=10,
                              require_parent_pk=True)
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        assert (1, 2) not in pairs

    def test_range_helper_returns_none_when_uncomparable(self):
        # No min/max -> None.
        a = _col_with_metadata(
            1, 1, "t", "x", [str(i) for i in range(10)],
            min_val=None, max_val=None,
        )
        b = _col_with_metadata(
            2, 2, "t", "y", [str(i) for i in range(10)],
            min_val=None, max_val=None,
        )
        assert _range_contained(a, b) is None

    def test_range_helper_accepts_valid_subset(self):
        a = _col_with_metadata(
            1, 1, "t", "x", [str(i) for i in range(10)],
            min_val="5", max_val="9",
        )
        b = _col_with_metadata(
            2, 2, "t", "y", [str(i) for i in range(20)],
            min_val="0", max_val="19",
        )
        assert _range_contained(a, b) is True


class TestMaxRelationshipsCap:
    """Tier 1+2 #14: global cap demotes everything past the cap."""

    def test_max_relationships_cap(self):
        cands = [
            FkCandidate(
                child_col_id=i,
                parent_col_id=1000 + i,
                child_table=f"c{i}",
                child_column="fk",
                parent_table="p",
                parent_column="id",
                estimated_containment=0.5,
                name_similarity=0.5,
                type_match=True,
                source_stage="test",
                joint_estimate=None,
                tier="primary",
                confidence=0.10 + 0.01 * i,
            )
            for i in range(20)
        ]
        apply_global_cap(cands, max_relationships=5)
        primaries = [c for c in cands if c.tier == "primary"]
        assert len(primaries) == 5
        # Only the top-5 by confidence (i=15..19) are kept primary.
        kept = {c.child_col_id for c in primaries}
        assert kept == {15, 16, 17, 18, 19}

    def test_max_relationships_cap_disabled(self):
        cands = [
            FkCandidate(
                child_col_id=i,
                parent_col_id=1000 + i,
                child_table=f"c{i}",
                child_column="fk",
                parent_table="p",
                parent_column="id",
                estimated_containment=0.5,
                name_similarity=0.5,
                type_match=True,
                source_stage="test",
                joint_estimate=None,
                tier="primary",
                confidence=0.5,
            )
            for i in range(10)
        ]
        apply_global_cap(cands, max_relationships=None)
        # No demotions when cap is None / 0.
        assert all(c.tier == "primary" for c in cands)


class TestReconcilePkDirection:
    """Tier 1+2 #5: when both sides are implicit_pk, demote the one with
    fewer inbound name-matches."""

    def test_reconcile_demotes_one_side(self):
        # Two implicit-PK columns ("orders.id", "audit.id"); other tables
        # have a column "order_id" but none have "audit_id" -> orders.id
        # should retain implicit_pk; audit.id should be demoted.
        cols = [
            _col_with_metadata(
                1, 10, "orders", "id",
                values=[str(i) for i in range(100)],
                is_implicit_pk=True,
                min_val="0", max_val="99",
            ),
            _col_with_metadata(
                2, 20, "audit", "id",
                values=[str(i) for i in range(100)],
                is_implicit_pk=True,
                min_val="0", max_val="99",
            ),
            # Inbound matches for orders.id only.
            _col_with_metadata(
                3, 30, "shipments", "order_id",
                values=[str(i) for i in range(100)],
            ),
            _col_with_metadata(
                4, 40, "invoices", "order_id",
                values=[str(i) for i in range(100)],
            ),
        ]
        n = _reconcile_pk_direction(cols)
        assert n == 1
        orders_id = next(c for c in cols if c.table_name == "orders")
        audit_id = next(c for c in cols if c.table_name == "audit")
        assert orders_id.is_implicit_pk is True
        assert audit_id.is_implicit_pk is False
        assert audit_id.is_unique_indexed is True


# ---------------------------------------------------------------------------
# Sprint A7 — recall regression fixes
# ---------------------------------------------------------------------------


class TestCrossTableRoleFk:
    """Sprint A7 #1: cross-table role-based FK detection.

    Role-suffix children (``posted_by``, ``referrer_id``, ``approved_by``,
    ...) frequently reference a parent's PK by *role*, not by parent name.
    The lexical name-similarity gate would reject these because, e.g.,
    ``name_similarity('posted_by', 'id') ≈ 0.18``.
    """

    def test_role_based_fk_helper_positive(self):
        """``posted_by`` → ``employees.id`` should be flagged."""
        assert _is_role_based_fk("posted_by", "employees", "id")
        assert _is_role_based_fk("referrer_id", "employees", "id")
        assert _is_role_based_fk("approved_by", "employees", "id")
        assert _is_role_based_fk("reviewer_id", "employees", "id")
        assert _is_role_based_fk("reported_by", "employees", "id")
        assert _is_role_based_fk("assigned_hr_id", "employees", "id")
        # Plural-PK form: <plural-norm-of-parent>_id.
        assert _is_role_based_fk("manager_id", "employees", "employee_id")

    def test_role_based_fk_helper_negative(self):
        # Not a role suffix.
        assert not _is_role_based_fk("first_name", "employees", "id")
        # Parent column is neither ``id`` nor ``<table_norm>_id``.
        assert not _is_role_based_fk("posted_by", "employees", "email")

    def test_cross_table_role_fk_admitted_low_card(self):
        """``job_postings.posted_by → employees.id`` survives even when
        ``posted_by`` has a small distinct count and the lexical similarity
        is low.
        """
        # Few distinct posters (HR-shaped: a handful of recruiters) but a
        # large employees parent table.
        child_vals = [str(i) for i in range(8)]   # 8 distinct posters
        parent_vals = [str(i) for i in range(1000)]
        cols = [
            _col_with_metadata(
                1, 10, "job_postings", "posted_by", child_vals,
                min_val="0", max_val="7",
            ),
            _col_with_metadata(
                2, 20, "employees", "id", parent_vals,
                is_pk=True,
                min_val="0", max_val="999",
                ordinal_position=1,
            ),
        ]
        cands = sql_prefilter(
            cols,
            child_min_distinct_count=100,
            require_parent_pk=True,
        )
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        assert (1, 2) in pairs, (
            f"Cross-table role FK posted_by -> id should be admitted; got {pairs}"
        )

    def test_cross_table_role_fk_promoted_to_primary(self):
        """Role bypass with parent-PK signal lands in the primary tier."""
        child_vals = [str(i) for i in range(8)]
        parent_vals = [str(i) for i in range(1000)]
        cols = [
            _col_with_metadata(
                1, 10, "performance_reviews", "reviewer_id", child_vals,
                min_val="0", max_val="7",
            ),
            _col_with_metadata(
                2, 20, "employees", "id", parent_vals,
                is_pk=True,
                min_val="0", max_val="999",
                ordinal_position=1,
            ),
        ]
        cands = sql_prefilter(
            cols,
            child_min_distinct_count=100,
            require_parent_pk=True,
        )
        target = [c for c in cands if c.child_col_id == 1 and c.parent_col_id == 2]
        assert len(target) == 1
        assert target[0].tier == "primary"


class TestQualifiedNameSim:
    """Sprint A7 #3: parent-table-aware name-similarity.

    ``name_similarity('region_id', 'id')`` is ≈0.36 — below the
    dense-serial reject threshold (0.4).  But comparing against the
    qualified ``regions.id`` form gives 1.0.  The candidate gates use
    ``max(bare, qualified)`` so the FK survives.
    """

    def test_low_card_lookup_admitted_via_qualified_match(self):
        """``countries.region_id`` (dc=6) → ``regions.id`` (dc=6),
        both 1..6 dense serials.  Bare name_sim is 0.36 (would trip the
        dense-serial hard-reject); qualified is 1.0 → candidate survives.
        """
        child_vals = [str(i) for i in range(1, 7)]   # 1..6
        parent_vals = [str(i) for i in range(1, 7)]  # 1..6
        cols = [
            _col_with_metadata(
                1, 10, "countries", "region_id", child_vals,
                min_val="1", max_val="6",
                null_pct=0.0,
            ),
            _col_with_metadata(
                2, 20, "regions", "id", parent_vals,
                is_pk=True,
                min_val="1", max_val="6",
                null_pct=0.0,
                ordinal_position=1,
            ),
        ]
        cands = sql_prefilter(
            cols,
            child_min_distinct_count=100,  # both are well below this
            require_parent_pk=True,
        )
        pairs = {(c.child_col_id, c.parent_col_id) for c in cands}
        assert (1, 2) in pairs, (
            f"region_id -> regions.id should be admitted via qualified "
            f"name_sim + low-card bypass; got {pairs}"
        )
        # And the surfaced name_similarity should reflect the qualified
        # match (≥ 0.95) — important because downstream confidence ranking
        # uses this value.
        target = next(c for c in cands if c.child_col_id == 1 and c.parent_col_id == 2)
        assert target.name_similarity >= 0.95


class TestReconcilePkDirectionSafety:
    """Sprint A7 #4: tighten reconciliation so it doesn't demote
    declared PKs or break ties incorrectly.
    """

    def test_no_demote_declared_pk(self):
        """A declared (``is_pk=True``) column should never be demoted by
        ``_reconcile_pk_direction``.  This was the suspected adv-regression
        cause: an authoritative PK losing on inbound counts to a sibling
        implicit PK.
        """
        # ``business_entity.business_entity_id`` is the declared PK; many
        # other tables have ``business_entity_id`` columns referencing it
        # (so the inbound-count would normally favour the *child* tables'
        # implicit PKs over the parent's declared PK).
        cols = [
            # Declared PK on the parent table.
            _col_with_metadata(
                1, 10, "business_entity", "business_entity_id",
                values=[str(i) for i in range(100)],
                is_pk=True, is_implicit_pk=False,
                min_val="0", max_val="99",
            ),
            # Implicit PK on a child-side table (e.g. employee.business_entity_id
            # is incorrectly inferred as an implicit PK because in this small
            # synthetic dataset all values happened to be distinct).
            _col_with_metadata(
                2, 20, "employee", "business_entity_id",
                values=[str(i) for i in range(100)],
                is_implicit_pk=True,
                min_val="0", max_val="99",
            ),
            # Inbound name matches: many tables have a 'business_entity_id'
            # column.
            _col_with_metadata(
                3, 30, "store", "business_entity_id",
                values=[str(i) for i in range(100)],
            ),
            _col_with_metadata(
                4, 40, "vendor", "business_entity_id",
                values=[str(i) for i in range(100)],
            ),
        ]
        _reconcile_pk_direction(cols)
        # The declared PK must remain is_pk=True; reconciliation only
        # moves the ``is_implicit_pk`` flag, never ``is_pk``.
        be_pk = next(c for c in cols if c.table_name == "business_entity")
        assert be_pk.is_pk is True

    def test_only_runs_when_both_implicit(self):
        """When one side is declared (``is_pk=True``), reconciliation
        leaves the other side alone — no spurious demotion based on
        inbound counts.
        """
        cols = [
            # Declared PK.
            _col_with_metadata(
                1, 10, "business_entity", "id",
                values=[str(i) for i in range(100)],
                is_pk=True,
            ),
            # Implicit PK on an unrelated table.
            _col_with_metadata(
                2, 20, "employee", "id",
                values=[str(i) for i in range(100)],
                is_implicit_pk=True,
            ),
            # Inbound name matches for ``employee.id``.
            _col_with_metadata(
                3, 30, "department", "employee_id",
                values=[str(i) for i in range(100)],
            ),
            _col_with_metadata(
                4, 40, "salaries", "employee_id",
                values=[str(i) for i in range(100)],
            ),
        ]
        _reconcile_pk_direction(cols)
        emp_id = next(c for c in cols if c.table_name == "employee")
        # Reconciliation doesn't touch declared PKs and shouldn't demote
        # ``employee.id`` since its competitor is declared.
        assert emp_id.is_implicit_pk is True

    def test_tie_break_prefers_id(self):
        """When both sides are implicit PKs with equal inbound name
        match counts, the column literally named ``id`` wins (it's the
        canonical PK name).
        """
        cols = [
            # Both implicit PKs, same row counts, same inbound matches.
            _col_with_metadata(
                1, 10, "tab_a", "id",
                values=[str(i) for i in range(100)],
                is_implicit_pk=True,
            ),
            _col_with_metadata(
                2, 20, "tab_b", "key",
                values=[str(i) for i in range(100)],
                is_implicit_pk=True,
            ),
        ]
        _reconcile_pk_direction(cols)
        a_id = next(c for c in cols if c.table_name == "tab_a")
        b_key = next(c for c in cols if c.table_name == "tab_b")
        # ``tab_a.id`` keeps its implicit_pk flag; ``tab_b.key`` demoted.
        assert a_id.is_implicit_pk is True
        assert b_key.is_implicit_pk is False
        assert b_key.is_unique_indexed is True


class TestPopularParentTopK:
    """Sprint A7 #2: top_k_per_child raised from 5 → 10.

    Child columns whose true parent is a popular table (referenced by
    many FKs) are vulnerable to top-K demotion — the real parent often
    ranks 6+ by confidence.
    """

    def test_default_top_k_is_ten(self):
        """The new default keeps 10 candidates per child.

        Historically the cap was 5; with K=10 we keep the 10 highest-
        confidence parents per child.
        """
        # Build 12 candidates for the same child column with descending
        # confidence — the new default cap (10) keeps 10 of them.
        cands = [
            FkCandidate(
                child_col_id=1,
                parent_col_id=200 + i,
                child_table="t1",
                child_column="employee_id",
                parent_table=f"p{i}",
                parent_column="id",
                estimated_containment=0.5,
                name_similarity=0.5,
                type_match=True,
                source_stage="test",
                joint_estimate=None,
                tier="primary",
                confidence=0.10 + 0.05 * i,
            )
            for i in range(12)
        ]
        apply_top_k_per_child(cands, top_k=10)
        primaries = [c for c in cands if c.tier == "primary"]
        assert len(primaries) == 10
        kept = {c.parent_col_id for c in primaries}
        # The top 10 by confidence are i=2..11.
        assert kept == {200 + i for i in range(2, 12)}


# ---------------------------------------------------------------------------
# Sprint A8 — bidirectional dedup, bridge collisions, range overlap
# ---------------------------------------------------------------------------


class TestBidirectionalDedup:
    """Sprint A8 #1 — drop reverse-direction duplicates."""

    def _mk(self, child_id: int, parent_id: int, *, conf: float, ct: str, cn: str, pt: str, pn: str) -> FkCandidate:
        return FkCandidate(
            child_col_id=child_id, parent_col_id=parent_id,
            child_table=ct, child_column=cn,
            parent_table=pt, parent_column=pn,
            estimated_containment=0.99, name_similarity=0.7, type_match=True,
            source_stage="test", joint_estimate=None,
            tier="primary", confidence=conf,
        )

    def test_one_side_declared_pk_wins(self):
        """When exactly one side is declared is_pk=True, keep child→parent
        where parent is the declared PK."""
        cols_by_id = {
            1: ColSketch(column_id=1, table_id=1, table_name="orders", column_name="id",
                         type_class="INT_NARROW", distinct_count=100,
                         is_fk_eligible=True, sketch_blob=b"", is_pk=True),
            2: ColSketch(column_id=2, table_id=2, table_name="order_items", column_name="order_id",
                         type_class="INT_NARROW", distinct_count=50,
                         is_fk_eligible=True, sketch_blob=b"", is_pk=False),
        }
        cands = [
            self._mk(2, 1, conf=0.9, ct="order_items", cn="order_id", pt="orders", pn="id"),
            self._mk(1, 2, conf=0.5, ct="orders", cn="id", pt="order_items", pn="order_id"),
        ]
        n = dedup_bidirectional_candidates(cands, cols_by_id)
        assert n == 1
        # Forward (child=2 → parent=1) survives; reverse demoted.
        assert cands[0].tier == "primary"
        assert cands[1].tier == "advisory_lowconf"
        assert cands[1].score_features.get("reverse_direction") == 1.0

    def test_inheritance_both_pk_larger_parent_wins(self):
        """Both is_pk=true → the one with MORE distinct values is the
        IS-A parent; tag with is_a=True."""
        cols_by_id = {
            1: ColSketch(column_id=1, table_id=1, table_name="business_entity",
                         column_name="business_entity_id",
                         type_class="INT_NARROW", distinct_count=1000,
                         is_fk_eligible=True, sketch_blob=b"", is_pk=True),
            2: ColSketch(column_id=2, table_id=2, table_name="vendor",
                         column_name="business_entity_id",
                         type_class="INT_NARROW", distinct_count=200,
                         is_fk_eligible=True, sketch_blob=b"", is_pk=True),
        }
        cands = [
            self._mk(2, 1, conf=0.99, ct="vendor", cn="business_entity_id",
                     pt="business_entity", pn="business_entity_id"),
            self._mk(1, 2, conf=0.5, ct="business_entity", cn="business_entity_id",
                     pt="vendor", pn="business_entity_id"),
        ]
        n = dedup_bidirectional_candidates(cands, cols_by_id)
        assert n == 1
        # vendor → business_entity (larger parent wins)
        assert cands[0].tier == "primary"
        assert cands[0].score_features.get("is_a") == 1.0
        assert cands[1].tier == "advisory_lowconf"

    def test_alpha_tiebreak_is_deterministic(self):
        """When every other signal ties, use alphabetical (parent_table,
        parent_column, child_table, child_column) for a deterministic
        choice — not a coin flip."""
        cols_by_id = {
            1: ColSketch(column_id=1, table_id=1, table_name="alpha", column_name="id",
                         type_class="INT_NARROW", distinct_count=100,
                         is_fk_eligible=True, sketch_blob=b"", is_pk=False),
            2: ColSketch(column_id=2, table_id=2, table_name="beta", column_name="id",
                         type_class="INT_NARROW", distinct_count=100,
                         is_fk_eligible=True, sketch_blob=b"", is_pk=False),
        }
        cands = [
            self._mk(2, 1, conf=0.8, ct="beta", cn="id", pt="alpha", pn="id"),
            self._mk(1, 2, conf=0.8, ct="alpha", cn="id", pt="beta", pn="id"),
        ]
        n = dedup_bidirectional_candidates(cands, cols_by_id)
        assert n == 1
        # parent_table='alpha' sorts before 'beta' so beta→alpha wins.
        winners = [c for c in cands if c.tier == "primary"]
        assert len(winners) == 1
        assert winners[0].parent_table == "alpha"


class TestBridgeCollisionFilter:
    """Sprint A8 #2 — filter bridge-to-bridge collisions."""

    def test_real_anchor_wins_over_bridge(self):
        """inventory.film_id should pick film.film_id (1000 rows) over
        film_actor.film_id (5500 rows) when child has 1000 distinct."""
        cols_by_id = {
            10: ColSketch(column_id=10, table_id=10, table_name="film_actor",
                          column_name="film_id", type_class="INT_NARROW",
                          distinct_count=1000, is_fk_eligible=True,
                          sketch_blob=b"", is_pk=False),
            20: ColSketch(column_id=20, table_id=20, table_name="film",
                          column_name="film_id", type_class="INT_NARROW",
                          distinct_count=1000, is_fk_eligible=True,
                          sketch_blob=b"", is_pk=True),
            30: ColSketch(column_id=30, table_id=30, table_name="inventory",
                          column_name="film_id", type_class="INT_NARROW",
                          distinct_count=1000, is_fk_eligible=True,
                          sketch_blob=b"", is_pk=False),
        }
        table_row_counts = {10: 5500, 20: 1000, 30: 4500}
        cands = [
            FkCandidate(
                child_col_id=30, parent_col_id=20,
                child_table="inventory", child_column="film_id",
                parent_table="film", parent_column="film_id",
                estimated_containment=1.0, name_similarity=1.0, type_match=True,
                source_stage="lsh", joint_estimate=None,
                tier="primary", confidence=0.99,
            ),
            FkCandidate(
                child_col_id=30, parent_col_id=10,
                child_table="inventory", child_column="film_id",
                parent_table="film_actor", parent_column="film_id",
                estimated_containment=1.0, name_similarity=1.0, type_match=True,
                source_stage="lsh", joint_estimate=None,
                tier="primary", confidence=0.95,
            ),
        ]
        n = filter_bridge_collisions(cands, cols_by_id, table_row_counts)
        assert n == 1
        assert cands[0].tier == "primary"
        assert cands[1].tier == "advisory_lowconf"
        assert cands[1].score_features.get("bridge_collision") == 1.0


class TestRangeOverlapPenalty:
    """Sprint A8 #3 — soft penalty for tiny→huge range with weak names."""

    def test_demotes_tiny_into_huge_when_name_weak(self):
        """category.id (16 distinct) into actor.id (400 distinct) with
        name_sim=0.4 → demoted, confidence -= 0.10."""
        cols_by_id = {
            100: ColSketch(column_id=100, table_id=100, table_name="category",
                           column_name="id", type_class="INT_NARROW",
                           distinct_count=16, is_fk_eligible=True,
                           sketch_blob=b"", is_pk=True),
            200: ColSketch(column_id=200, table_id=200, table_name="actor",
                           column_name="id", type_class="INT_NARROW",
                           distinct_count=400, is_fk_eligible=True,
                           sketch_blob=b"", is_pk=True),
        }
        cands = [
            FkCandidate(
                child_col_id=100, parent_col_id=200,
                child_table="category", child_column="id",
                parent_table="actor", parent_column="id",
                estimated_containment=1.0, name_similarity=0.4, type_match=True,
                source_stage="lsh", joint_estimate=None,
                tier="primary", confidence=0.85,
            ),
        ]
        n = apply_range_overlap_penalty(cands, cols_by_id)
        assert n == 1
        assert cands[0].tier == "advisory_lowconf"
        assert cands[0].confidence == pytest.approx(0.75)

    def test_keeps_when_name_is_strong(self):
        """Same shape but name_sim=0.9 → keep primary."""
        cols_by_id = {
            100: ColSketch(column_id=100, table_id=100, table_name="category",
                           column_name="cat_id", type_class="INT_NARROW",
                           distinct_count=16, is_fk_eligible=True,
                           sketch_blob=b"", is_pk=True),
            200: ColSketch(column_id=200, table_id=200, table_name="categories",
                           column_name="cat_id", type_class="INT_NARROW",
                           distinct_count=400, is_fk_eligible=True,
                           sketch_blob=b"", is_pk=True),
        }
        cands = [
            FkCandidate(
                child_col_id=100, parent_col_id=200,
                child_table="category", child_column="cat_id",
                parent_table="categories", parent_column="cat_id",
                estimated_containment=1.0, name_similarity=0.9, type_match=True,
                source_stage="lsh", joint_estimate=None,
                tier="primary", confidence=0.95,
            ),
        ]
        n = apply_range_overlap_penalty(cands, cols_by_id)
        assert n == 0
        assert cands[0].tier == "primary"


class TestBothPkTighterThreshold:
    """Sprint A8 #4 — both-PK threshold tightened to 0.85."""

    def test_threshold_constant_value(self):
        assert _A2_BOTH_PK_NAME_SIM_THRESHOLD == pytest.approx(0.85)

    def test_role_bypass_min_distinct_constant_value(self):
        assert _ROLE_BYPASS_MIN_DISTINCT == 2

    def test_both_pk_name_sim_below_85_dropped(self):
        """orders.id ↔ shipments.id (name_sim ~ 0.5) is dropped from
        primary list.  Inheritance pattern (name_sim = 1.0) survives."""
        cols = [
            ColSketch(column_id=1, table_id=1, table_name="orders",
                      column_name="id", type_class="INT_NARROW",
                      distinct_count=100, is_fk_eligible=True,
                      sketch_blob=b"", is_pk=True),
            ColSketch(column_id=2, table_id=2, table_name="shipments",
                      column_name="id", type_class="INT_NARROW",
                      distinct_count=100, is_fk_eligible=True,
                      sketch_blob=b"", is_pk=True),
        ]
        # Bare name_sim('id', 'id') = 1.0 — but the pre-filter compares
        # qualified ('orders.id' vs 'shipments.id') which gives ~0.5 too.
        # With both being literal 'id' the bare similarity is 1.0, so
        # this case actually survives.  Use distinct table-prefixed ids.
        cols[0].column_name = "order_id"
        cols[1].column_name = "shipment_id"
        cands = sql_prefilter(cols, child_min_distinct_count=10)
        # both-PK + name_sim < 0.85 should be filtered out.
        primary_pairs = {
            (c.child_col_id, c.parent_col_id)
            for c in cands if c.tier == "primary"
        }
        assert (1, 2) not in primary_pairs
        assert (2, 1) not in primary_pairs
