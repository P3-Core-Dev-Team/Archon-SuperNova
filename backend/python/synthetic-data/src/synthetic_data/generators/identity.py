"""Generators for users, roles, user_roles, user_sessions, api_tokens."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from typing import Iterator

import numpy as np
import pyarrow as pa

from synthetic_data import config
from synthetic_data.distributions import diurnal_timestamps, uniform_timestamps
from synthetic_data.generators.base import (
    BaseTableGenerator,
    make_timestamps_array,
    make_uuid_array,
    make_nullable_int64,
    make_bool_array,
    make_date_array,
)
from synthetic_data.relationships import sample_fk_values

_REF_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)

_ROLE_NAMES = [
    "admin", "user", "manager", "support", "analyst",
    "developer", "tester", "viewer", "editor", "moderator",
    "billing", "shipping", "marketing", "sales", "hr",
    "finance", "legal", "security", "devops", "product",
]


class RolesGenerator(BaseTableGenerator):
    spec = config.ROLES

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.ROLES, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(100)
        n = self.row_count
        ids = np.arange(1, n + 1, dtype=np.int32)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)
        names = _ROLE_NAMES[:n]
        descriptions = [f"Role for {name} access" for name in names]
        batch = pa.record_batch({
            "id": pa.array(ids, type=pa.int32()),
            "name": pa.array(names, type=pa.string()),
            "description": pa.array(descriptions, type=pa.string()),
            "created_at": make_timestamps_array(ts),
        })
        yield batch

    @property
    def ids(self) -> np.ndarray:
        return np.arange(1, self.row_count + 1, dtype=np.int32)


class UsersGenerator(BaseTableGenerator):
    spec = config.USERS

    def __init__(self, customer_ids: np.ndarray, seed: int = 42, scale: float = 1.0):
        super().__init__(config.USERS, seed=seed, scale=scale)
        self.customer_ids = customer_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(110)
        srng = self._make_stdlib_rng(110)
        fake = self._make_faker(110)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)

        # customer_id: 30% null (staff users)
        cust_fk = sample_fk_values(self.customer_ids, n, null_pct=0.30, rng=rng)

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 4, rng=rng)

        # DOBs: 10% null, ages 18-70
        ages = rng.integers(18, 71, size=n)
        dob_arr = make_date_array(ages, ref_year=2026)
        dob_null = rng.random(n) < 0.1

        is_active = rng.random(n) > 0.05
        is_verified = rng.random(n) > 0.15

        usernames = [fake.user_name() for _ in range(n)]
        emails = [fake.email() for _ in range(n)]

        # password_hash: sha256 of a random string
        password_hashes = []
        for i in range(n):
            raw = f"pw_{i}_{self.seed}".encode()
            password_hashes.append(hashlib.sha256(raw).digest())

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            size = end - offset

            dob_col = [None if dob_null[i] else dob_arr[i].as_py() for i in range(offset, end)]

            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "customer_id": make_nullable_int64(cust_fk[sl]),
                "username": pa.array(usernames[offset:end], type=pa.string()),
                "email": pa.array(emails[offset:end], type=pa.string()),
                "dob": pa.array(dob_col, type=pa.date32()),
                "password_hash": pa.array(password_hashes[offset:end], type=pa.binary()),
                "is_active": make_bool_array(is_active[sl]),
                "is_verified": make_bool_array(is_verified[sl]),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end

    @property
    def ids(self) -> np.ndarray:
        return np.arange(1, self.row_count + 1, dtype=np.int64)


class UserRolesGenerator(BaseTableGenerator):
    spec = config.USER_ROLES

    def __init__(
        self,
        user_ids: np.ndarray,
        role_ids: np.ndarray,
        seed: int = 42,
        scale: float = 1.0,
    ):
        super().__init__(config.USER_ROLES, seed=seed, scale=scale)
        self.user_ids = user_ids
        self.role_ids = role_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(120)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        user_fk = sample_fk_values(self.user_ids, n, rng=rng)
        role_fk = sample_fk_values(self.role_ids.astype(np.int64), n, rng=rng)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "user_id": pa.array(user_fk[sl].astype(np.int64), type=pa.int64()),
                "role_id": pa.array(role_fk[sl].astype(np.int32), type=pa.int32()),
                "granted_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class UserSessionsGenerator(BaseTableGenerator):
    spec = config.USER_SESSIONS

    def __init__(self, user_ids: np.ndarray, seed: int = 42, scale: float = 1.0):
        super().__init__(config.USER_SESSIONS, seed=seed, scale=scale)
        self.user_ids = user_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(130)
        srng = self._make_stdlib_rng(130)
        fake = self._make_faker(130)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        user_fk = sample_fk_values(self.user_ids, n, rng=rng)

        # Diurnal timestamps: last 30 days
        started_ts = diurnal_timestamps(_REF_DT, n, span_days=30, rng=rng)
        # Ended_at: 20% null (active sessions), rest 0-4 hours later
        ended_null = rng.random(n) < 0.2
        ended_ts = started_ts + rng.integers(300, 14_400, size=n)

        ip_list = [fake.ipv4() for _ in range(n)]
        ua_list = [fake.user_agent() for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)

            ended_col = [None if ended_null[i] else int(ended_ts[i]) for i in range(offset, end)]

            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "session_id": make_uuid_array(end - offset, rng),
                "user_id": pa.array(user_fk[sl].astype(np.int64), type=pa.int64()),
                "started_at": make_timestamps_array(started_ts[sl]),
                "ended_at": pa.array(
                    [None if v is None else v * 1_000_000 for v in ended_col],
                    type=pa.timestamp("us", tz="UTC")
                ),
                "ip_address": pa.array(ip_list[offset:end], type=pa.string()),
                "user_agent": pa.array(ua_list[offset:end], type=pa.string()),
            })
            yield batch
            offset = end


class ApiTokensGenerator(BaseTableGenerator):
    spec = config.API_TOKENS

    def __init__(self, user_ids: np.ndarray, seed: int = 42, scale: float = 1.0):
        super().__init__(config.API_TOKENS, seed=seed, scale=scale)
        self.user_ids = user_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(140)
        srng = self._make_stdlib_rng(140)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        user_fk = sample_fk_values(self.user_ids, n, rng=rng)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)

        # expires_at: 30% null (no expiry)
        expires_null = rng.random(n) < 0.3
        expires_ts = ts + rng.integers(86400 * 30, 86400 * 365, size=n)

        # Tokens: 40-char high-entropy strings (deterministic via rng)
        _ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        tokens = []
        secret_hashes = []
        for i in range(n):
            raw_bytes = rng.integers(0, 256, size=30, dtype=np.uint8)
            token = base64.urlsafe_b64encode(bytes(raw_bytes.tolist())).decode("ascii")[:40]
            tokens.append(token)
            secret_hashes.append(hashlib.sha256(token.encode()).hexdigest())

        names = [f"token_{i + 1}" for i in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)

            expires_col = [
                None if expires_null[i] else int(expires_ts[i]) * 1_000_000
                for i in range(offset, end)
            ]

            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "user_id": pa.array(user_fk[sl].astype(np.int64), type=pa.int64()),
                "token": pa.array(tokens[offset:end], type=pa.string()),
                "secret_hash": pa.array(secret_hashes[offset:end], type=pa.string()),
                "name": pa.array(names[offset:end], type=pa.string()),
                "expires_at": pa.array(expires_col, type=pa.timestamp("us", tz="UTC")),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end
