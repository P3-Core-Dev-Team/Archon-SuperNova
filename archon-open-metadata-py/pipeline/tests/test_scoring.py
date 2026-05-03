"""
Unit tests for ``discovery.scoring`` — pure helpers used by Phase 4 and
Phase 5.

Surfaces under test:
  * ``is_dense_serial`` — detects 1..N surrogate-key columns (Q3 / A3).
  * ``compute_confidence`` — weighted multi-feature confidence (M1 / A4).
  * ``_normalise_name`` / ``name_similarity`` / ``is_self_ref_role`` —
    name-similarity helpers for the lexical gate.

No DB, no FAISS, no DuckDB.
"""
from __future__ import annotations

import pytest

from discovery.scoring import (
    _normalise_name,
    compute_confidence,
    is_dense_serial,
    is_role_based_fk,
    is_self_ref_role,
    name_similarity,
)


# ---------------------------------------------------------------------------
# is_dense_serial
# ---------------------------------------------------------------------------


class TestIsDenseSerial:
    """Tests for the 1..N surrogate-key heuristic."""

    def test_classic_dense_serial(self) -> None:
        assert is_dense_serial(
            distinct_count=1000,
            null_pct=0.0,
            type_class="INT_NARROW",
            min_val="1",
            max_val="1000",
        )

    def test_int_wide_also_qualifies(self) -> None:
        assert is_dense_serial(
            distinct_count=10_000,
            null_pct=0.0,
            type_class="INT_WIDE",
            min_val="1",
            max_val="10000",
        )

    def test_text_columns_rejected(self) -> None:
        # UUIDs and strings can't be dense serials regardless of stats.
        assert not is_dense_serial(
            distinct_count=1000,
            null_pct=0.0,
            type_class="UUID",
            min_val="1",
            max_val="1000",
        )

    def test_min_not_one_rejected(self) -> None:
        # Sequence starting at 100 is not a 1..N dense serial.
        assert not is_dense_serial(
            distinct_count=1000,
            null_pct=0.0,
            type_class="INT_NARROW",
            min_val="100",
            max_val="1099",
        )

    def test_sparse_range_rejected(self) -> None:
        # Range much wider than distinct count — sparse, not dense.
        assert not is_dense_serial(
            distinct_count=1000,
            null_pct=0.0,
            type_class="INT_NARROW",
            min_val="1",
            max_val="100000",
        )

    def test_high_null_pct_rejected(self) -> None:
        assert not is_dense_serial(
            distinct_count=1000,
            null_pct=0.10,
            type_class="INT_NARROW",
            min_val="1",
            max_val="1000",
        )

    def test_off_by_one_tolerated(self) -> None:
        # max_val == distinct_count - 1 (could happen with 0..N-1 zero-based).
        assert is_dense_serial(
            distinct_count=1000,
            null_pct=0.0,
            type_class="INT_NARROW",
            min_val="1",
            max_val="999",
        )

    def test_unparseable_min_max_rejected(self) -> None:
        assert not is_dense_serial(
            distinct_count=1000,
            null_pct=0.0,
            type_class="INT_NARROW",
            min_val="abc",
            max_val="xyz",
        )

    def test_none_distinct_rejected(self) -> None:
        assert not is_dense_serial(
            distinct_count=None,
            null_pct=0.0,
            type_class="INT_NARROW",
            min_val="1",
            max_val="1000",
        )

    def test_none_null_pct_treated_as_zero(self) -> None:
        # Inventory may store null_pct as NULL on a never-null column; that
        # should be permissive, not a rejection.
        assert is_dense_serial(
            distinct_count=1000,
            null_pct=None,
            type_class="INT_NARROW",
            min_val="1",
            max_val="1000",
        )


# ---------------------------------------------------------------------------
# compute_confidence
# ---------------------------------------------------------------------------


class TestComputeConfidence:
    """Tests for the multi-feature confidence formula."""

    def test_perfect_signals_yield_high_score(self) -> None:
        score = compute_confidence(
            containment_full=1.0,
            name_similarity=1.0,
            parent_is_pk=True,
            parent_is_unique_indexed=True,
            child_distinct=500,
            parent_distinct=1000,
            sketch_jaccard=0.5,
        )
        # 0.40*1 + 0.30*1 + 0.15*1 + 0.10*1 + 0.05*0.5 = 0.975
        assert score == pytest.approx(0.975, abs=1e-3)

    def test_zero_signals_zero_score(self) -> None:
        score = compute_confidence(
            containment_full=0.0,
            name_similarity=0.0,
            parent_is_pk=False,
            parent_is_unique_indexed=False,
            child_distinct=0,
            parent_distinct=0,
            sketch_jaccard=0.0,
        )
        assert score == pytest.approx(0.0, abs=1e-6)

    def test_pk_outranks_unique_indexed(self) -> None:
        """Postgres PKs are always unique-indexed; PK weight must dominate."""
        as_pk = compute_confidence(
            containment_full=0.5,
            name_similarity=0.5,
            parent_is_pk=True,
            parent_is_unique_indexed=True,
            child_distinct=100,
            parent_distinct=200,
        )
        as_unique = compute_confidence(
            containment_full=0.5,
            name_similarity=0.5,
            parent_is_pk=False,
            parent_is_unique_indexed=True,
            child_distinct=100,
            parent_distinct=200,
        )
        assert as_pk > as_unique
        # 0.15 vs 0.075 → exactly 0.075 difference.
        assert (as_pk - as_unique) == pytest.approx(0.075, abs=1e-3)

    def test_score_bounded_in_zero_one(self) -> None:
        """Even with absurd inputs, score must stay in [0, 1]."""
        for cont, name in [(2.0, 2.0), (-1.0, -1.0), (0.5, 1.5)]:
            s = compute_confidence(
                containment_full=cont,
                name_similarity=name,
                parent_is_pk=True,
                parent_is_unique_indexed=True,
                child_distinct=100,
                parent_distinct=200,
            )
            assert 0.0 <= s <= 1.0

    def test_card_ratio_penalises_overflow(self) -> None:
        good = compute_confidence(
            containment_full=0.5,
            name_similarity=0.5,
            parent_is_pk=True,
            parent_is_unique_indexed=True,
            child_distinct=100,
            parent_distinct=200,
        )
        # Child has 3× parent → impossible FK direction → card_score=0.
        bad = compute_confidence(
            containment_full=0.5,
            name_similarity=0.5,
            parent_is_pk=True,
            parent_is_unique_indexed=True,
            child_distinct=600,
            parent_distinct=200,
        )
        assert good > bad
        # 0.10 * card_score (1.0 vs 0.0) = exactly 0.10 spread.
        assert (good - bad) == pytest.approx(0.10, abs=1e-3)

    def test_realistic_orders_customers_pair(self) -> None:
        """orders.customer_id → customers.id, plausible signals."""
        score = compute_confidence(
            containment_full=0.97,
            name_similarity=0.55,    # difflib customer_id vs id
            parent_is_pk=True,
            parent_is_unique_indexed=True,
            child_distinct=8_000,
            parent_distinct=10_000,
            sketch_jaccard=0.78,
        )
        # 0.40*0.97 + 0.30*0.55 + 0.15*1.0 + 0.10*1.0 + 0.05*0.78 = 0.842
        assert score == pytest.approx(0.842, abs=1e-2)

    def test_no_signals_partial(self) -> None:
        """Just containment, nothing else — should be low but nonzero."""
        score = compute_confidence(
            containment_full=0.95,
            name_similarity=0.0,
            parent_is_pk=False,
            parent_is_unique_indexed=False,
            child_distinct=100,
            parent_distinct=200,
        )
        # 0.40*0.95 + 0.10*1.0 = 0.48
        assert score == pytest.approx(0.48, abs=1e-2)


# ---------------------------------------------------------------------------
# _normalise_name
# ---------------------------------------------------------------------------


def test_normalise_name() -> None:
    """Normalisation of common column-name shapes."""
    assert _normalise_name("Customers") == "customer"
    assert _normalise_name("product_id") == "product"
    assert _normalise_name("IDs") == "id"
    # 'id' should survive unchanged: stripping '_id' would empty the
    # string and the empty-result guard keeps it intact.
    assert _normalise_name("id") == "id"


# ---------------------------------------------------------------------------
# name_similarity
# ---------------------------------------------------------------------------


def test_name_similarity_plural() -> None:
    """Plural / suffix normalisation collapses customer_id ~ customers.id."""
    # Strong match: same root, just plural + dotted PK form.
    assert name_similarity("customer_id", "customers.id") >= 0.95
    # Distinct roots stay below the precision-gate threshold (0.7).
    assert name_similarity("client_id", "customer_id") < 0.7


def test_name_similarity_no_normalize() -> None:
    """plural_normalize=False yields a strictly lower (or equal) ratio."""
    with_norm = name_similarity("customer_id", "customers.id")
    raw = name_similarity(
        "customer_id", "customers.id", plural_normalize=False
    )
    # Raw should be strictly less than the normalised score for this pair.
    assert raw < with_norm


def test_name_similarity_unrelated() -> None:
    """Truly unrelated columns score very low even after normalisation."""
    assert name_similarity("created_at", "customer_id") < 0.4


# ---------------------------------------------------------------------------
# is_self_ref_role
# ---------------------------------------------------------------------------


def test_is_self_ref_role_positive() -> None:
    """employee.manager_id -> employee.id is a textbook self-ref role FK."""
    assert is_self_ref_role(
        child_table="employee",
        child_column="manager_id",
        parent_table="employee",
        parent_column="id",
    )


def test_is_self_ref_role_negative() -> None:
    """Different parent table → not a self-ref."""
    assert not is_self_ref_role(
        child_table="employee",
        child_column="manager_id",
        parent_table="department",
        parent_column="id",
    )


def test_is_self_ref_role_not_pk_target() -> None:
    """Parent column must be the table's PK ('id' or '<table>_id')."""
    assert not is_self_ref_role(
        child_table="employee",
        child_column="manager_id",
        parent_table="employee",
        parent_column="email",
    )


# ---------------------------------------------------------------------------
# is_role_based_fk (Sprint A7 #1) — cross-table role-based FK
# ---------------------------------------------------------------------------


class TestIsRoleBasedFk:
    """``is_role_based_fk`` recognises cross-table role-suffix FKs.

    Independent of whether child_table == parent_table — it only
    checks the *child column name* against the role-suffix list and
    the *parent column name* against the parent table's PK convention.
    """

    def test_posted_by_employees_id(self) -> None:
        """``job_postings.posted_by → employees.id`` is a textbook
        cross-table role-based FK.
        """
        assert is_role_based_fk("posted_by", "employees", "id")

    def test_referrer_id_employees_id(self) -> None:
        assert is_role_based_fk("referrer_id", "employees", "id")

    def test_approved_by_employees_id(self) -> None:
        assert is_role_based_fk("approved_by", "employees", "id")

    def test_reviewer_id_employees_id(self) -> None:
        assert is_role_based_fk("reviewer_id", "employees", "id")

    def test_reported_by_employees_id(self) -> None:
        assert is_role_based_fk("reported_by", "employees", "id")

    def test_assigned_hr_id_employees_id(self) -> None:
        assert is_role_based_fk("assigned_hr_id", "employees", "id")

    def test_role_based_fk_plural_pk(self) -> None:
        """Parent column may be ``<plural-norm-of-parent>_id`` rather
        than bare ``id`` (e.g. employees → employee_id).
        """
        assert is_role_based_fk("manager_id", "employees", "employee_id")

    def test_role_based_fk_non_role_column_rejected(self) -> None:
        """Plain (non-role-suffix) child columns are NOT role-based FKs."""
        assert not is_role_based_fk("first_name", "employees", "id")
        assert not is_role_based_fk("amount", "employees", "id")

    def test_role_based_fk_non_pk_target_rejected(self) -> None:
        """Parent column must be a PK convention (``id`` or
        ``<plural-norm>_id``).
        """
        assert not is_role_based_fk("posted_by", "employees", "email")
        assert not is_role_based_fk("referrer_id", "employees", "name")

    def test_role_based_fk_case_insensitive(self) -> None:
        """Helper is case-insensitive on column / table names."""
        assert is_role_based_fk("POSTED_BY", "Employees", "ID")
