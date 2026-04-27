"""
Unit tests for fingerprint.py (Phase 3a).

No database required — pure Parquet + algorithm tests.
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from discovery.fingerprint import ColumnFingerprint, fingerprint_column


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tiny_parquet(tmp_path: Path) -> Path:
    """
    Write a small Parquet file with:
      - 'id'   : 100 distinct integers (perfect cardinality test)
      - 'cat'  : 5 distinct strings repeated 20 times
      - 'nulls': 50 non-null + 50 null integers
    """
    n = 100
    table = pa.table({
        "id":    pa.array(list(range(n)), type=pa.int64()),
        "cat":   pa.array(["cat_a", "cat_b", "cat_c", "cat_d", "cat_e"] * 20, type=pa.string()),
        "nulls": pa.array([i if i < 50 else None for i in range(n)], type=pa.int64()),
    })
    path = tmp_path / "tiny.parquet"
    pq.write_table(table, str(path), compression="zstd")
    return path


@pytest.fixture
def many_row_parquet(tmp_path: Path) -> Path:
    """
    Write a Parquet file with 20 000 distinct integer values
    to exercise the HLL++ path (above exact_distinct_below=10000).
    """
    n = 20_000
    table = pa.table({
        "big_id": pa.array(list(range(n)), type=pa.int64()),
    })
    path = tmp_path / "big.parquet"
    pq.write_table(table, str(path), compression="zstd")
    return path


# ---------------------------------------------------------------------------
# Basic smoke tests
# ---------------------------------------------------------------------------

def test_fingerprint_returns_correct_type(tiny_parquet: Path):
    fp = fingerprint_column(tiny_parquet, "id")
    assert isinstance(fp, ColumnFingerprint)


def test_row_count_exact(tiny_parquet: Path):
    fp = fingerprint_column(tiny_parquet, "id")
    assert fp.row_count == 100


def test_null_count_and_pct(tiny_parquet: Path):
    fp = fingerprint_column(tiny_parquet, "nulls")
    assert fp.null_count == 50
    assert abs(fp.null_pct - 0.5) < 1e-6


def test_no_nulls(tiny_parquet: Path):
    fp = fingerprint_column(tiny_parquet, "id")
    assert fp.null_count == 0
    assert fp.null_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Cardinality tests
# ---------------------------------------------------------------------------

def test_cardinality_exact_for_small(tiny_parquet: Path):
    """100 distinct values < exact_distinct_below=10000 → method should be 'exact'."""
    fp = fingerprint_column(tiny_parquet, "id", exact_distinct_below=10_000)
    assert fp.cardinality_method == "exact"
    assert fp.cardinality_estimate == 100


def test_cardinality_exact_cat(tiny_parquet: Path):
    """5 distinct categories — exact count expected."""
    fp = fingerprint_column(tiny_parquet, "cat", exact_distinct_below=10_000)
    assert fp.cardinality_method == "exact"
    assert fp.cardinality_estimate == 5


def test_cardinality_hll_for_large(many_row_parquet: Path):
    """20 000 distinct values > exact_distinct_below=10000 → method should be 'hll++'."""
    fp = fingerprint_column(many_row_parquet, "big_id", exact_distinct_below=10_000)
    # HLL++ with p=14 has ~1% error
    assert fp.cardinality_method == "hll++"
    assert abs(fp.cardinality_estimate - 20_000) / 20_000 < 0.05  # within 5%


def test_cardinality_within_5pct(many_row_parquet: Path):
    """Explicit 5% tolerance requirement from the spec."""
    fp = fingerprint_column(many_row_parquet, "big_id")
    error_pct = abs(fp.cardinality_estimate - 20_000) / 20_000
    assert error_pct < 0.05, f"Cardinality error {error_pct:.2%} exceeds 5%"


# ---------------------------------------------------------------------------
# Sketch blob tests
# ---------------------------------------------------------------------------

def test_sketch_blob_nonempty(tiny_parquet: Path):
    fp = fingerprint_column(tiny_parquet, "id")
    assert fp.sketch_blob
    assert len(fp.sketch_blob) > 0


def test_sketch_blob_deserializable(tiny_parquet: Path):
    """Sketch blob must be a valid pickle."""
    import pickle
    fp = fingerprint_column(tiny_parquet, "id")
    obj = pickle.loads(fp.sketch_blob)
    # Should have either .jaccard() (MinHash / HyperMinHash) or .count() (HLL)
    assert hasattr(obj, "jaccard") or hasattr(obj, "count")


def test_sketcher_kind_recorded(tiny_parquet: Path):
    fp = fingerprint_column(tiny_parquet, "id", sketcher="hyperminhash")
    # Either 'hyperminhash' (if installed) or 'minhash' (fallback)
    assert fp.sketcher_kind in ("hyperminhash", "minhash")


def test_minhash_fallback(tiny_parquet: Path):
    """Requesting minhash explicitly should always succeed."""
    fp = fingerprint_column(tiny_parquet, "id", sketcher="minhash", num_perm=128)
    assert fp.sketcher_kind == "minhash"


# ---------------------------------------------------------------------------
# Min/max value tests
# ---------------------------------------------------------------------------

def test_min_max_not_none(tiny_parquet: Path):
    fp = fingerprint_column(tiny_parquet, "id")
    assert fp.min_val is not None
    assert fp.max_val is not None


def test_all_null_column(tmp_path: Path):
    """All-null column should have null_pct=1.0 and min_val/max_val=None."""
    table = pa.table({"x": pa.array([None, None, None], type=pa.int64())})
    path = tmp_path / "allnull.parquet"
    pq.write_table(table, str(path))
    fp = fingerprint_column(path, "x")
    assert fp.null_pct == pytest.approx(1.0)
    assert fp.null_count == 3
    assert fp.min_val is None
    assert fp.max_val is None


# ---------------------------------------------------------------------------
# Sketch similarity sanity check
# ---------------------------------------------------------------------------

def test_similar_columns_have_higher_jaccard_than_dissimilar(tmp_path: Path):
    """
    Column A and B are identical → Jaccard should be > Jaccard(A, C) where C is random.
    """
    import pickle
    import random

    random.seed(0)
    n = 500
    vals_ab = [str(i) for i in range(n)]
    vals_c = [str(random.randint(100_000, 200_000)) for _ in range(n)]

    table = pa.table({
        "a": pa.array(vals_ab, type=pa.string()),
        "b": pa.array(vals_ab, type=pa.string()),   # identical to a
        "c": pa.array(vals_c, type=pa.string()),    # unrelated
    })
    path = tmp_path / "sim.parquet"
    pq.write_table(table, str(path))

    fp_a = fingerprint_column(path, "a", sketcher="minhash", num_perm=128, exact_distinct_below=0)
    fp_b = fingerprint_column(path, "b", sketcher="minhash", num_perm=128, exact_distinct_below=0)
    fp_c = fingerprint_column(path, "c", sketcher="minhash", num_perm=128, exact_distinct_below=0)

    sk_a = pickle.loads(fp_a.sketch_blob)
    sk_b = pickle.loads(fp_b.sketch_blob)
    sk_c = pickle.loads(fp_c.sketch_blob)

    j_ab = sk_a.jaccard(sk_b)
    j_ac = sk_a.jaccard(sk_c)

    assert j_ab > j_ac, f"Expected j_ab ({j_ab:.3f}) > j_ac ({j_ac:.3f})"
    assert j_ab > 0.9, f"Identical columns should have Jaccard > 0.9, got {j_ab:.3f}"


# ---------------------------------------------------------------------------
# C3: Adaptive HLL early-stop
# ---------------------------------------------------------------------------


@pytest.fixture
def stable_multi_rg_parquet(tmp_path: Path) -> Path:
    """
    Build a parquet file with 5 row groups, each containing the same 100
    distinct integer values.  The HLL estimate stabilises after the first
    row group, so the adaptive early-stop should fire well before reading
    all 5 row groups.
    """
    n = 100
    vals = list(range(n))
    # 5 identical row groups via row_group_size=n on a 5*n-row table.
    data = vals * 5
    table = pa.table({"x": pa.array(data, type=pa.int64())})
    path = tmp_path / "stable.parquet"
    pq.write_table(table, str(path), row_group_size=n)
    return path


def test_early_stop_fires_on_stable_data(stable_multi_rg_parquet: Path):
    """
    With identical data across 5 row groups, the HLL stabilises immediately
    and the adaptive early-stop must trigger before reading all 5 RGs.
    """
    fp = fingerprint_column(
        stable_multi_rg_parquet,
        "x",
        early_stop_delta=0.05,
        exact_distinct_below=0,
    )
    assert fp.row_groups_total == 5
    assert fp.early_stopped is True
    assert fp.row_groups_read < 5, (
        f"Expected early stop, got row_groups_read={fp.row_groups_read} "
        f"of {fp.row_groups_total}"
    )
    # With ≥3 row groups + 2 consecutive sub-threshold deltas required, the
    # earliest break is after reading exactly 3 row groups.
    assert fp.row_groups_read >= 3


def test_early_stop_disabled_when_threshold_zero(stable_multi_rg_parquet: Path):
    """early_stop_delta=0 must read every row group (no break path)."""
    fp = fingerprint_column(
        stable_multi_rg_parquet,
        "x",
        early_stop_delta=0.0,
        exact_distinct_below=0,
    )
    assert fp.row_groups_read == 5
    assert fp.early_stopped is False


def test_early_stop_does_not_fire_on_growing_cardinality(tmp_path: Path):
    """
    With each row group adding 100 NEW distinct values, the HLL estimate
    grows by ~25% per RG, well above any reasonable threshold — the
    adaptive early-stop must NOT fire.
    """
    n = 100
    rgs = 5
    data = list(range(n * rgs))  # 500 fully distinct values
    table = pa.table({"x": pa.array(data, type=pa.int64())})
    path = tmp_path / "growing.parquet"
    pq.write_table(table, str(path), row_group_size=n)

    fp = fingerprint_column(
        path,
        "x",
        early_stop_delta=0.005,
        exact_distinct_below=0,
    )
    assert fp.row_groups_total == 5
    assert fp.early_stopped is False
    assert fp.row_groups_read == 5
