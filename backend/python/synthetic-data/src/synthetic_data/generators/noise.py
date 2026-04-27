"""Generators for all noise tables (excluded by pattern matching)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import numpy as np
import pyarrow as pa

from synthetic_data import config
from synthetic_data.distributions import uniform_timestamps
from synthetic_data.generators.base import (
    BaseTableGenerator,
    make_timestamps_array,
    make_uuid_array,
    make_nullable_int64,
    make_bool_array,
)

_REF_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class AuditLogGenerator(BaseTableGenerator):
    spec = config.AUDIT_LOG

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.AUDIT_LOG, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(200)
        n = self.row_count
        _TABLES = ["customers", "orders", "products", "users", "payments"]
        _OPS = ["INSERT", "UPDATE", "DELETE"]

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)
        user_ids = rng.integers(0, 10_001, size=n).astype(np.float64)
        user_ids[rng.random(n) < 0.05] = np.nan

        srng = self._make_stdlib_rng(200)
        table_names = [srng.choice(_TABLES) for _ in range(n)]
        ops = [srng.choice(_OPS) for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "table_name": pa.array(table_names[offset:end], type=pa.string()),
                "operation": pa.array(ops[offset:end], type=pa.string()),
                "user_id": make_nullable_int64(user_ids[sl]),
                "old_values": pa.array([f'{{"id": {i}}}' for i in ids[sl].tolist()], type=pa.large_utf8()),
                "new_values": pa.array([f'{{"id": {i}, "v": 2}}' for i in ids[sl].tolist()], type=pa.large_utf8()),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class AccessLogGenerator(BaseTableGenerator):
    spec = config.ACCESS_LOG

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.ACCESS_LOG, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(210)
        srng = self._make_stdlib_rng(210)
        fake = self._make_faker(210)
        n = self.row_count

        _METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]
        _PATHS = ["/api/v1/customers", "/api/v1/orders", "/api/v1/products",
                  "/api/v1/payments", "/api/v1/users", "/health", "/metrics"]
        _STATUS = [200, 200, 200, 201, 400, 401, 403, 404, 500]

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)

        user_ids = rng.integers(0, 10_001, size=n).astype(np.float64)
        user_ids[rng.random(n) < 0.1] = np.nan

        ips = [fake.ipv4() for _ in range(n)]
        methods = [srng.choice(_METHODS) for _ in range(n)]
        paths = [srng.choice(_PATHS) for _ in range(n)]
        status_codes = [srng.choice(_STATUS) for _ in range(n)]
        response_times = rng.integers(1, 2000, size=n, dtype=np.int64)

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "user_id": make_nullable_int64(user_ids[sl]),
                "ip_address": pa.array(ips[offset:end], type=pa.string()),
                "method": pa.array(methods[offset:end], type=pa.string()),
                "path": pa.array(paths[offset:end], type=pa.string()),
                "status_code": pa.array(status_codes[offset:end], type=pa.int32()),
                "response_time_ms": pa.array(response_times[sl], type=pa.int64()),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class TempImportBatchGenerator(BaseTableGenerator):
    spec = config.TEMP_IMPORT_BATCH

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.TEMP_IMPORT_BATCH, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(220)
        srng = self._make_stdlib_rng(220)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=30, rng=rng)
        _SOURCES = ["salesforce", "shopify", "legacy_erp", "external_api", "csv_import"]
        _STATUSES = ["pending", "processing", "complete", "failed"]

        sources = [srng.choice(_SOURCES) for _ in range(n)]
        statuses = [srng.choice(_STATUSES) for _ in range(n)]
        record_data = [f'{{"batch_row": {i}}}' for i in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "batch_id": make_uuid_array(end - offset, rng),
                "source_system": pa.array(sources[offset:end], type=pa.string()),
                "record_data": pa.array(record_data[offset:end], type=pa.large_utf8()),
                "status": pa.array(statuses[offset:end], type=pa.string()),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class TmpStagingOrdersGenerator(BaseTableGenerator):
    spec = config.TMP_STAGING_ORDERS

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.TMP_STAGING_ORDERS, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(230)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=14, rng=rng)
        processed = rng.random(n) > 0.3

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "external_order_id": pa.array(
                    [f"EXT-{i:08d}" for i in ids[sl].tolist()], type=pa.string()
                ),
                "raw_json": pa.array(
                    [f'{{"order_id": {i}}}' for i in ids[sl].tolist()], type=pa.large_utf8()
                ),
                "processed": make_bool_array(processed[sl]),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class OrdersBakGenerator(BaseTableGenerator):
    spec = config.ORDERS_BAK_20240101

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.ORDERS_BAK_20240101, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(240)
        srng = self._make_stdlib_rng(240)
        n = self.row_count

        _STATUSES = ["pending", "shipped", "delivered", "cancelled"]
        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)
        customer_ids = rng.integers(1, 50_001, size=n, dtype=np.int64)
        amounts = rng.uniform(10, 10_000, size=n)
        statuses = [srng.choice(_STATUSES) for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "customer_id": pa.array(customer_ids[sl], type=pa.int64()),
                "total_amount": pa.array(amounts[sl], type=pa.float64()),
                "status": pa.array(statuses[offset:end], type=pa.string()),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class CustomersArchiveGenerator(BaseTableGenerator):
    spec = config.CUSTOMERS_ARCHIVE

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.CUSTOMERS_ARCHIVE, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(250)
        fake = self._make_faker(250)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 5, rng=rng)
        archived_ts = ts + rng.integers(86400, 86400 * 365, size=n)

        first_names = [fake.first_name() for _ in range(n)]
        last_names = [fake.last_name() for _ in range(n)]
        emails = [fake.email() for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "first_name": pa.array(first_names[offset:end], type=pa.string()),
                "last_name": pa.array(last_names[offset:end], type=pa.string()),
                "email": pa.array(emails[offset:end], type=pa.string()),
                "archived_at": make_timestamps_array(archived_ts[sl]),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class UserEventsGenerator(BaseTableGenerator):
    spec = config.USER_EVENTS

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.USER_EVENTS, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(260)
        srng = self._make_stdlib_rng(260)
        n = self.row_count

        _EVENTS = ["page_view", "click", "purchase", "login", "logout",
                   "search", "add_to_cart", "checkout", "review", "share"]

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=365, rng=rng)
        user_ids = rng.integers(1, 10_001, size=n, dtype=np.int64)
        event_types = [srng.choice(_EVENTS) for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "user_id": pa.array(user_ids[sl], type=pa.int64()),
                "event_type": pa.array(event_types[offset:end], type=pa.string()),
                "event_data": pa.array(
                    [f'{{"type": "{et}"}}' for et in event_types[offset:end]], type=pa.string()
                ),
                "session_id": make_uuid_array(end - offset, rng),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class EtlImportQueueGenerator(BaseTableGenerator):
    spec = config.ETL_IMPORT_QUEUE

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.ETL_IMPORT_QUEUE, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(270)
        srng = self._make_stdlib_rng(270)
        n = self.row_count

        _STATUSES = ["queued", "running", "success", "failed"]
        _TABLES = ["customers", "orders", "products", "users"]

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=90, rng=rng)
        statuses = [srng.choice(_STATUSES) for _ in range(n)]
        src_tables = [srng.choice(_TABLES) for _ in range(n)]
        tgt_tables = [f"{t}_staging" for t in src_tables]
        error_null = rng.random(n) < 0.8
        errors = [None if error_null[i] else f"Error processing {src_tables[i]}" for i in range(n)]

        batch = pa.record_batch({
            "id": pa.array(ids, type=pa.int64()),
            "source_table": pa.array(src_tables, type=pa.string()),
            "target_table": pa.array(tgt_tables, type=pa.string()),
            "status": pa.array(statuses, type=pa.string()),
            "error_msg": pa.array(errors, type=pa.string()),
            "created_at": make_timestamps_array(ts),
        })
        yield batch


class MigrationsGenerator(BaseTableGenerator):
    spec = config.MIGRATIONS

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.MIGRATIONS, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(280)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int32)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)

        versions = [f"V{i + 1:03d}__migration" for i in range(n)]
        descriptions = [f"Migration {i + 1}: schema change" for i in range(n)]

        batch = pa.record_batch({
            "id": pa.array(ids, type=pa.int32()),
            "version": pa.array(versions, type=pa.string()),
            "description": pa.array(descriptions, type=pa.string()),
            "applied_at": make_timestamps_array(ts),
        })
        yield batch
