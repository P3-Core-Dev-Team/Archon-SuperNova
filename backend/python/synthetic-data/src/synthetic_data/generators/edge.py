"""Generators for edge-case tables: wide_denormalized and empty_table."""

from __future__ import annotations

from typing import Iterator

import numpy as np
import pyarrow as pa

from synthetic_data import config
from synthetic_data.generators.base import (
    BaseTableGenerator,
)


class WideDenormalizedGenerator(BaseTableGenerator):
    spec = config.WIDE_DENORMALIZED

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.WIDE_DENORMALIZED, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(290)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)

        # Spec: id + col_001..col_249 = 250 columns total.
        # No created_at — it would make the parquet 251 cols and disagree
        # with config.WIDE_DENORMALIZED / the manifest.
        columns: dict[str, pa.Array] = {
            "id": pa.array(ids, type=pa.int64()),
        }

        for i in range(1, 250):
            col_name = f"col_{i:03d}"
            vals = rng.random(n)
            if i % 5 == 0:
                # 5% null
                mask = rng.random(n) < 0.05
                float_arr = vals.astype(np.float64)
                float_arr[mask] = np.nan
                null_mask = np.isnan(float_arr)
                safe_vals = np.where(null_mask, 0.0, float_arr)
                columns[col_name] = pa.array(safe_vals, type=pa.float64(), mask=null_mask)
            else:
                columns[col_name] = pa.array(vals, type=pa.float64())

        # Yield in batches
        keys = list(columns.keys())
        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            batch_cols = {k: columns[k].slice(offset, end - offset) for k in keys}
            yield pa.record_batch(batch_cols)
            offset = end


class EmptyTableGenerator(BaseTableGenerator):
    spec = config.EMPTY_TABLE

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.EMPTY_TABLE, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        # Yield nothing — write_parquet handles empty case
        return
        yield  # make it a generator
