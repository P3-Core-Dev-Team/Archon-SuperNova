"""BaseTableGenerator — streaming Parquet writer with 100K row batches."""

from __future__ import annotations

import abc
import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from synthetic_data.config import TableSpec, TypeClass


class BaseTableGenerator(abc.ABC):
    """Abstract base for all table generators."""

    name: str
    spec: TableSpec
    BATCH_SIZE: int = 100_000

    def __init__(self, spec: TableSpec, seed: int = 42, scale: float = 1.0):
        self.spec = spec
        self.name = spec.name
        self.seed = seed
        self.scale = scale
        self.row_count = max(0, int(spec.row_count * scale))
        # Dim tables: don't scale below minimum viable size
        if spec.row_count <= 50 and scale < 1.0:
            self.row_count = spec.row_count  # never scale tiny dimension tables

    def _make_rng(self, sub_seed: int = 0) -> np.random.Generator:
        """Create a seeded numpy Generator for this table."""
        combined = (self.seed * 31337 + sub_seed) & 0xFFFFFFFF
        return np.random.default_rng(combined)

    def _make_stdlib_rng(self, sub_seed: int = 0):
        """Create a seeded stdlib random.Random for this table."""
        import random
        combined = (self.seed * 31337 + sub_seed + 1) & 0xFFFFFFFF
        r = random.Random(combined)
        return r

    def _make_faker(self, sub_seed: int = 0):
        """Create a seeded Faker instance."""
        from faker import Faker
        combined = (self.seed * 31337 + sub_seed + 2) & 0xFFFFFFFF
        fake = Faker(["en_US", "en_GB", "de_DE", "fr_FR"])
        Faker.seed(combined)
        fake.seed_instance(combined)
        return fake

    @abc.abstractmethod
    def batches(self) -> Iterator[pa.RecordBatch]:
        """Yield RecordBatches of BATCH_SIZE rows."""

    def pyarrow_schema(self) -> pa.Schema:
        """Build pyarrow schema from the TableSpec."""
        fields = []
        for col in self.spec.columns:
            pa_type = _type_to_arrow(col.type_class)
            if col.nullable or col.null_pct > 0:
                fields.append(pa.field(col.name, pa_type, nullable=True))
            else:
                fields.append(pa.field(col.name, pa_type, nullable=False))
        return pa.schema(fields)

    def write_parquet(
        self,
        path: str | Path,
        compression: str = "zstd",
        compression_level: int = 3,
    ) -> int:
        """
        Write all batches to a Parquet file.
        Returns total number of rows written.
        """
        path = Path(path)
        schema = self.pyarrow_schema()
        total_rows = 0

        if self.row_count == 0:
            # Write empty Parquet with correct schema
            table = pa.table({f.name: pa.array([], type=f.type) for f in schema}, schema=schema)
            pq.write_table(
                table,
                path,
                compression=compression,
                compression_level=compression_level,
                row_group_size=self.BATCH_SIZE,
                write_statistics=True,
            )
            return 0

        writer = None
        try:
            for batch in self.batches():
                if writer is None:
                    writer = pq.ParquetWriter(
                        path,
                        batch.schema,
                        compression=compression,
                        compression_level=compression_level,
                    )
                writer.write_batch(batch)
                total_rows += batch.num_rows
        finally:
            if writer is not None:
                writer.close()

        return total_rows


def _type_to_arrow(tc: TypeClass) -> pa.DataType:
    mapping = {
        TypeClass.INT_NARROW: pa.int32(),
        TypeClass.INT_WIDE: pa.int64(),
        TypeClass.UUID: pa.string(),
        TypeClass.STRING_SHORT: pa.string(),
        TypeClass.STRING_LONG: pa.large_utf8(),
        TypeClass.DATE: pa.date32(),
        TypeClass.TIMESTAMP: pa.timestamp("us", tz="UTC"),
        TypeClass.BOOL: pa.bool_(),
        TypeClass.FLOAT: pa.float64(),
        TypeClass.BINARY: pa.binary(),
    }
    return mapping[tc]


def make_timestamps_array(ts_seconds: np.ndarray, tz: str = "UTC") -> pa.Array:
    """Convert int64 seconds-since-epoch to pyarrow timestamp array (microseconds)."""
    ts_us = ts_seconds.astype(np.int64) * 1_000_000
    return pa.array(ts_us, type=pa.timestamp("us", tz=tz))


def make_nullable_int64(arr: np.ndarray) -> pa.Array:
    """Convert float64 array (NaN = null) to nullable int64 pyarrow array."""
    if arr.dtype == np.float64:
        mask = np.isnan(arr)
        # Convert valid floats to int64
        int_arr = np.where(mask, 0, arr).astype(np.int64)
        return pa.array(int_arr, type=pa.int64(), mask=mask)
    return pa.array(arr, type=pa.int64())


def make_nullable_int32(arr: np.ndarray) -> pa.Array:
    """Convert float64 array (NaN = null) to nullable int32 pyarrow array."""
    if arr.dtype == np.float64:
        mask = np.isnan(arr)
        int_arr = np.where(mask, 0, arr).astype(np.int32)
        return pa.array(int_arr, type=pa.int32(), mask=mask)
    return pa.array(arr, type=pa.int32())


def make_bool_array(arr: np.ndarray) -> pa.Array:
    return pa.array(arr.astype(bool), type=pa.bool_())


def make_uuid_array(n: int, rng) -> pa.Array:
    """Generate n UUID strings deterministically using rng.bytes."""
    uuids = []
    for _ in range(n):
        raw = rng.integers(0, 256, size=16, dtype=np.uint8)
        # Set version 4 bits
        raw[6] = (raw[6] & 0x0F) | 0x40
        raw[8] = (raw[8] & 0x3F) | 0x80
        b = bytes(raw.tolist())
        uuids.append(str(uuid.UUID(bytes=b)))
    return pa.array(uuids, type=pa.string())


def make_date_array(ages_years: np.ndarray, ref_year: int = 2024) -> pa.Array:
    """Convert age-in-years array to date32 array."""
    # ref_date = Jan 1 of ref_year
    import datetime as dt
    ref_date = dt.date(ref_year, 1, 1)
    ref_ord = ref_date.toordinal()
    # date32 is days since epoch (1970-01-01)
    epoch_ord = dt.date(1970, 1, 1).toordinal()
    dob_ordinals = ref_ord - (ages_years * 365.25).astype(np.int64)
    days_since_epoch = dob_ordinals - epoch_ord
    return pa.array(days_since_epoch.astype(np.int32), type=pa.date32())
