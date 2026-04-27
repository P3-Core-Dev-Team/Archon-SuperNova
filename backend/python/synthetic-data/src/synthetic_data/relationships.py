"""FK value sampling that guarantees actual containment."""

from __future__ import annotations

import numpy as np
from typing import Sequence


def sample_fk_values(
    parent_values: np.ndarray,
    n: int,
    cardinality: str = "many_to_one",
    null_pct: float = 0.0,
    zipfian_s: float = 0.0,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Sample `n` FK values from `parent_values`, guaranteeing containment.

    Parameters
    ----------
    parent_values : 1-D array of parent PK values
    n             : number of child rows to generate
    cardinality   : 'many_to_one' (default) or 'one_to_one'
    null_pct      : fraction of values to set to null (np.nan / -1 sentinel)
    zipfian_s     : if >0, use Zipfian weights (higher s = more skewed)
    rng           : seeded numpy Generator

    Returns
    -------
    int64 array of shape (n,) with -1 representing NULL
    """
    if rng is None:
        rng = np.random.default_rng()

    parent_arr = np.asarray(parent_values, dtype=np.int64)
    m = len(parent_arr)

    if zipfian_s > 0.0:
        k = np.arange(1, m + 1, dtype=np.float64)
        weights = 1.0 / np.power(k, zipfian_s)
        weights /= weights.sum()
        indices = rng.choice(m, size=n, replace=True, p=weights)
    elif cardinality == "one_to_one":
        if n > m:
            raise ValueError(
                f"Cannot sample {n} one_to_one FK values from {m} parents"
            )
        indices = rng.choice(m, size=n, replace=False)
    else:
        indices = rng.integers(0, m, size=n)

    result = parent_arr[indices]

    # Apply nulls
    if null_pct > 0.0:
        null_mask = rng.random(n) < null_pct
        result = result.astype(np.float64)
        result[null_mask] = np.nan
        # Keep as float64 to represent nullable int (Arrow handles None)
    else:
        result = result.astype(np.int64)

    return result


def ensure_all_parents_covered(
    parent_values: np.ndarray,
    child_fk_values: np.ndarray,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Ensure every parent value appears at least once in child_fk_values.
    Overwrites randomly-chosen non-null child values with missing parent values.
    """
    if rng is None:
        rng = np.random.default_rng()
    parent_set = set(parent_values.tolist())
    child_arr = child_fk_values.copy()
    # Indices of non-null values
    if child_arr.dtype == np.float64:
        non_null_idx = np.where(~np.isnan(child_arr))[0]
        present = set(int(v) for v in child_arr[non_null_idx])
    else:
        non_null_idx = np.arange(len(child_arr))
        present = set(child_arr.tolist())

    missing = list(parent_set - present)
    if not missing:
        return child_arr
    # Randomly replace non-null positions with missing parent values
    if len(non_null_idx) < len(missing):
        raise ValueError("Not enough non-null child rows to cover all parents")
    replace_idx = rng.choice(non_null_idx, size=len(missing), replace=False)
    for idx, pval in zip(replace_idx, missing):
        child_arr[idx] = pval
    return child_arr
