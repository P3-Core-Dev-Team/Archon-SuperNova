"""Statistical distribution helpers for realistic data generation."""

from __future__ import annotations

import numpy as np
from datetime import datetime, timedelta, timezone


def zipfian(n: int, size: int, s: float = 1.1, rng: np.random.Generator | None = None) -> np.ndarray:
    """
    Sample `size` values uniformly from [0, n) weighted by Zipf distribution.
    Returns integer indices into a pool of size n.
    Higher-ranked (lower-index) items appear more frequently.
    """
    if rng is None:
        rng = np.random.default_rng()
    # Compute Zipf weights w_k = 1/k^s for k=1..n
    k = np.arange(1, n + 1, dtype=np.float64)
    weights = 1.0 / np.power(k, s)
    weights /= weights.sum()
    return rng.choice(n, size=size, replace=True, p=weights)


def lognormal_int(mean: float, sigma: float, size: int,
                  lo: int = 1, hi: int = 100,
                  rng: np.random.Generator | None = None) -> np.ndarray:
    """
    Lognormal distribution clamped to [lo, hi] and rounded to integers.
    mean/sigma are in log-space (of the underlying normal).
    """
    if rng is None:
        rng = np.random.default_rng()
    vals = rng.lognormal(mean=mean, sigma=sigma, size=size)
    return np.clip(np.round(vals), lo, hi).astype(np.int64)


def lognormal_float(mean: float, sigma: float, size: int,
                    lo: float = 0.0, hi: float = 1e9,
                    rng: np.random.Generator | None = None) -> np.ndarray:
    """Lognormal distribution clamped to [lo, hi] as floats."""
    if rng is None:
        rng = np.random.default_rng()
    vals = rng.lognormal(mean=mean, sigma=sigma, size=size)
    return np.clip(vals, lo, hi).astype(np.float64)


# Business-hours weights (UTC, rough US East Coast pattern)
# Index = hour of day 0-23; higher = more orders placed
_BUSINESS_HOURS_RAW = np.array([
    0.1, 0.05, 0.03, 0.02, 0.02, 0.03,   # 00-05 (night)
    0.1,  0.3,  0.7,  1.0,  1.1,  1.2,   # 06-11 (morning ramp)
    1.1,  1.0,  0.9,  0.8,  0.9,  1.0,   # 12-17 (afternoon)
    1.2,  1.3,  1.1,  0.8,  0.5,  0.3,   # 18-23 (evening peak)
], dtype=np.float64)
BUSINESS_HOURS_WEIGHTS = _BUSINESS_HOURS_RAW / _BUSINESS_HOURS_RAW.sum()

# Diurnal pattern for user sessions (more activity during daytime)
_DIURNAL_RAW = np.array([
    0.05, 0.02, 0.01, 0.01, 0.02, 0.05,
    0.2,  0.6,  1.0,  1.3,  1.4,  1.3,
    1.2,  1.1,  1.0,  1.0,  1.1,  1.2,
    1.4,  1.5,  1.3,  0.9,  0.5,  0.2,
], dtype=np.float64)
DIURNAL_WEIGHTS = _DIURNAL_RAW / _DIURNAL_RAW.sum()


def business_hours_timestamps(
    base_dt: datetime,
    count: int,
    span_days: int = 365 * 3,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Generate `count` timestamps distributed over `span_days` days from `base_dt`
    with a business-hours hourly weighting. Returns int64 microseconds since epoch.
    """
    if rng is None:
        rng = np.random.default_rng()
    base_ts = int(base_dt.timestamp())
    # Random day offsets
    day_offsets = rng.integers(0, span_days, size=count)
    # Weighted hour-of-day
    hours = rng.choice(24, size=count, p=BUSINESS_HOURS_WEIGHTS)
    # Random minute/second
    minutes = rng.integers(0, 60, size=count)
    seconds = rng.integers(0, 60, size=count)
    ts = (base_ts
          + day_offsets * 86_400
          + hours * 3_600
          + minutes * 60
          + seconds)
    return ts.astype(np.int64)


def diurnal_timestamps(
    base_dt: datetime,
    count: int,
    span_days: int = 30,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Generate `count` timestamps in the last `span_days` days with diurnal weighting.
    Returns int64 seconds since epoch.
    """
    if rng is None:
        rng = np.random.default_rng()
    base_ts = int(base_dt.timestamp()) - span_days * 86_400
    day_offsets = rng.integers(0, span_days, size=count)
    hours = rng.choice(24, size=count, p=DIURNAL_WEIGHTS)
    minutes = rng.integers(0, 60, size=count)
    seconds = rng.integers(0, 60, size=count)
    ts = (base_ts
          + day_offsets * 86_400
          + hours * 3_600
          + minutes * 60
          + seconds)
    return ts.astype(np.int64)


def growth_curve_timestamps(
    base_dt: datetime,
    count: int,
    span_days: int = 365 * 5,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Simulate customer sign-up growth curve: exponential ramp-up.
    More recent dates appear more frequently (lognormal over the span).
    Returns int64 seconds since epoch.
    """
    if rng is None:
        rng = np.random.default_rng()
    base_ts = int(base_dt.timestamp()) - span_days * 86_400
    # Use beta distribution skewed toward the end of the period
    offsets = rng.beta(a=2.0, b=1.0, size=count)  # skewed toward 1.0 (recent)
    day_offsets = (offsets * span_days).astype(np.int64)
    hours = rng.integers(0, 24, size=count)
    minutes = rng.integers(0, 60, size=count)
    ts = (base_ts
          + day_offsets * 86_400
          + hours * 3_600
          + minutes * 60)
    return ts.astype(np.int64)


def uniform_timestamps(
    base_dt: datetime,
    count: int,
    span_days: int = 365 * 2,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Uniform random timestamps over the past `span_days`. Returns int64 seconds since epoch."""
    if rng is None:
        rng = np.random.default_rng()
    base_ts = int(base_dt.timestamp()) - span_days * 86_400
    offsets = rng.integers(0, span_days * 86_400, size=count)
    return (base_ts + offsets).astype(np.int64)
