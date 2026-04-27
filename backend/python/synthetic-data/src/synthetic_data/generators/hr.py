"""Generators for employee_records and departments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import numpy as np
import pyarrow as pa

from synthetic_data import config
from synthetic_data.distributions import lognormal_float, uniform_timestamps
from synthetic_data.generators.base import (
    BaseTableGenerator,
    make_timestamps_array,
    make_nullable_int64,
    make_nullable_int32,
    make_bool_array,
    make_date_array,
)
from synthetic_data.pii.phone import generate_phone_batch
from synthetic_data.pii.ssn import generate_ssn_batch, fake_ssn_999_batch
from synthetic_data.relationships import sample_fk_values

_REF_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class EmployeeRecordsGenerator(BaseTableGenerator):
    spec = config.EMPLOYEE_RECORDS

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.EMPLOYEE_RECORDS, seed=seed, scale=scale)
        self._ids: np.ndarray | None = None

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(150)
        srng = self._make_stdlib_rng(150)
        fake = self._make_faker(150)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        self._ids = ids

        # ~50 managers (null manager_id), rest reference a manager
        n_managers = min(50, max(1, n // 30))
        manager_ids = np.empty(n, dtype=np.float64)
        manager_ids[:n_managers] = np.nan  # top-level managers
        if n > n_managers:
            mgr_pool = ids[:n_managers].astype(np.float64)
            for i in range(n_managers, n):
                manager_ids[i] = float(srng.choice(mgr_pool))

        # Dates
        hire_ages = rng.integers(22, 60, size=n)
        hire_date_arr = make_date_array(hire_ages, ref_year=2026)
        dob_ages = hire_ages + rng.integers(0, 10, size=n)
        dob_arr = make_date_array(dob_ages, ref_year=2026)

        # PII
        full_names = [fake.name() for _ in range(n)]
        ssns = generate_ssn_batch(n, rng=srng)
        employee_ids = fake_ssn_999_batch(n, rng=srng)  # noise: 999-prefix
        work_emails = [fake.email() for _ in range(n)]

        # personal_email: 40% null
        personal_null = rng.random(n) < 0.40
        personal_emails = [fake.email() for _ in range(n)]

        phones = generate_phone_batch(n, rng=srng)

        # Salary: lognormal $30k-$500k
        salaries = lognormal_float(mean=11.0, sigma=0.6, size=n, lo=30_000, hi=500_000, rng=rng)

        is_active = rng.random(n) > 0.05

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 5, rng=rng)

        # department_id: nullable (5%), will be filled by departments later
        dept_null = rng.random(n) < 0.05
        dept_ids_raw = rng.integers(1, 31, size=n).astype(np.float64)
        dept_ids_raw[dept_null] = np.nan

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)

            personal_col = [
                None if personal_null[i] else personal_emails[i]
                for i in range(offset, end)
            ]

            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "employee_id": pa.array(employee_ids[offset:end], type=pa.string()),
                "full_name": pa.array(full_names[offset:end], type=pa.string()),
                "ssn": pa.array(ssns[offset:end], type=pa.string()),
                "dob": dob_arr.slice(offset, end - offset),
                "work_email": pa.array(work_emails[offset:end], type=pa.string()),
                "personal_email": pa.array(personal_col, type=pa.string()),
                "phone": pa.array(phones[offset:end], type=pa.string()),
                "hire_date": hire_date_arr.slice(offset, end - offset),
                "department_id": make_nullable_int32(dept_ids_raw[sl]),
                "manager_id": make_nullable_int64(manager_ids[sl]),
                "salary": pa.array(salaries[sl], type=pa.float64()),
                "is_active": make_bool_array(is_active[sl]),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end

    @property
    def ids(self) -> np.ndarray:
        if self._ids is None:
            self._ids = np.arange(1, self.row_count + 1, dtype=np.int64)
        return self._ids


class DepartmentsGenerator(BaseTableGenerator):
    spec = config.DEPARTMENTS

    def __init__(self, employee_ids: np.ndarray, seed: int = 42, scale: float = 1.0):
        super().__init__(config.DEPARTMENTS, seed=seed, scale=scale)
        self.employee_ids = employee_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(160)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int32)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)

        # head_employee_id: 10% null
        head_fk = sample_fk_values(self.employee_ids, n, null_pct=0.1, rng=rng)

        _DEPT_NAMES = [
            "Engineering", "Product", "Sales", "Marketing", "Finance",
            "Human Resources", "Legal", "Operations", "Customer Support",
            "Research & Development", "IT", "Security", "Data Science",
            "Design", "Business Development", "Procurement", "Logistics",
            "Quality Assurance", "Compliance", "Executive",
            "Infrastructure", "Analytics", "Communications", "Strategy",
            "Facilities", "Training", "Audit", "Risk Management",
            "Corporate Development", "Investor Relations",
        ]
        names = [_DEPT_NAMES[i % len(_DEPT_NAMES)] for i in range(n)]

        batch = pa.record_batch({
            "id": pa.array(ids, type=pa.int32()),
            "name": pa.array(names, type=pa.string()),
            "head_employee_id": make_nullable_int64(head_fk),
            "created_at": make_timestamps_array(ts),
        })
        yield batch
