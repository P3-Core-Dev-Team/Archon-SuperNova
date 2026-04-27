"""Generators for customers and addresses tables."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Iterator

import numpy as np
import pyarrow as pa

from synthetic_data import config
from synthetic_data.distributions import growth_curve_timestamps, uniform_timestamps
from synthetic_data.generators.base import (
    BaseTableGenerator,
    make_timestamps_array,
    make_uuid_array,
    make_date_array,
    make_nullable_int64,
    make_bool_array,
)
from synthetic_data.pii.phone import generate_phone_batch
from synthetic_data.relationships import sample_fk_values

_REF_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class CustomersGenerator(BaseTableGenerator):
    spec = config.CUSTOMERS

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.CUSTOMERS, seed=seed, scale=scale)
        self._ids: np.ndarray | None = None  # populated during generation

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(0)
        srng = self._make_stdlib_rng(0)
        fake = self._make_faker(0)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        self._ids = ids

        # Timestamps
        created_ts = growth_curve_timestamps(_REF_DT, n, span_days=365 * 5, rng=rng)
        updated_ts = created_ts + rng.integers(0, 86400 * 30, size=n)

        # DOBs: ages 18-85
        ages = rng.integers(18, 86, size=n)
        dob_arr = make_date_array(ages, ref_year=2026)

        # Active flags
        is_active = rng.random(n) > 0.05  # 95% active

        # Phone: 15% null
        null_phone_mask = rng.random(n) < 0.15
        phones = generate_phone_batch(n, rng=srng)

        # Names and emails via faker (process in batches for speed)
        first_names = []
        last_names = []
        emails = []
        for i in range(n):
            first_names.append(fake.first_name())
            last_names.append(fake.last_name())
            emails.append(fake.email())

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            size = end - offset
            sl = slice(offset, end)

            phone_col = [None if null_phone_mask[i] else phones[i] for i in range(offset, end)]

            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "external_id": make_uuid_array(size, rng),
                "first_name": pa.array(first_names[offset:end], type=pa.string()),
                "last_name": pa.array(last_names[offset:end], type=pa.string()),
                "email": pa.array(emails[offset:end], type=pa.string()),
                "phone": pa.array(phone_col, type=pa.string()),
                "dob": dob_arr.slice(offset, size),
                "is_active": make_bool_array(is_active[sl]),
                "created_at": make_timestamps_array(created_ts[sl]),
                "updated_at": make_timestamps_array(updated_ts[sl]),
            })
            yield batch
            offset = end

    @property
    def ids(self) -> np.ndarray:
        if self._ids is None:
            self._ids = np.arange(1, self.row_count + 1, dtype=np.int64)
        return self._ids


class AddressesGenerator(BaseTableGenerator):
    spec = config.ADDRESSES

    def __init__(self, customer_ids: np.ndarray, seed: int = 42, scale: float = 1.0):
        super().__init__(config.ADDRESSES, seed=seed, scale=scale)
        self.customer_ids = customer_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(10)
        srng = self._make_stdlib_rng(10)
        fake = self._make_faker(10)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)

        # FK: customer_id with 2% null
        cust_fk = sample_fk_values(
            self.customer_ids, n, null_pct=0.02,
            zipfian_s=0.0, rng=rng
        )

        # Timestamps
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)

        # is_primary
        is_primary = rng.random(n) < 0.4

        _STATES = ["CA", "NY", "TX", "FL", "IL", "WA", "MA", "CO", "GA", "OH",
                   "NC", "VA", "AZ", "MI", "NV", "OR", "MN", "WI", "MO", "IN"]
        _COUNTRIES = ["US"] * 90 + ["GB"] * 5 + ["CA"] * 3 + ["AU"] * 2

        # Generate address data
        streets = [fake.street_address() for _ in range(n)]
        cities = [fake.city() for _ in range(n)]
        states = [srng.choice(_STATES) for _ in range(n)]
        postal_codes = [fake.zipcode() for _ in range(n)]
        countries = [srng.choice(_COUNTRIES) for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)

            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "customer_id": make_nullable_int64(cust_fk[sl]),
                "street": pa.array(streets[offset:end], type=pa.string()),
                "city": pa.array(cities[offset:end], type=pa.string()),
                "state": pa.array(states[offset:end], type=pa.string()),
                "postal_code": pa.array(postal_codes[offset:end], type=pa.string()),
                "country_code": pa.array(countries[offset:end], type=pa.string()),
                "is_primary": make_bool_array(is_primary[sl]),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end

    @property
    def ids(self) -> np.ndarray:
        return np.arange(1, self.row_count + 1, dtype=np.int64)
