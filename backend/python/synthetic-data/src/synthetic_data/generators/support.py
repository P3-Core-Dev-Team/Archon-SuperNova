"""Generators for tickets, ticket_messages, and reviews."""

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
    make_nullable_int64,
    make_bool_array,
)
from synthetic_data.pii.free_text import generate_free_text_batch
from synthetic_data.relationships import sample_fk_values

_REF_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)

_TICKET_STATUSES = ["open", "pending", "resolved", "closed", "escalated"]
_TICKET_PRIORITIES = ["low", "medium", "high", "urgent"]
_TICKET_SUBJECTS = [
    "Order not received",
    "Payment issue",
    "Product damaged",
    "Wrong item shipped",
    "Refund request",
    "Account login problem",
    "Subscription cancellation",
    "Billing discrepancy",
    "Product quality complaint",
    "Shipping delay",
    "Missing parts",
    "Technical support needed",
    "Discount code not working",
    "Return request",
    "Change delivery address",
]

_REVIEW_TITLES = [
    "Excellent product!",
    "Good value",
    "Not as described",
    "Great quality",
    "Disappointed",
    "Highly recommended",
    "Works perfectly",
    "Poor quality",
    "Fast shipping",
    "Will buy again",
    "Average product",
    "Exceeded expectations",
    "Not worth the price",
    "Great for the money",
    "Terrible experience",
]


class TicketsGenerator(BaseTableGenerator):
    spec = config.TICKETS

    def __init__(
        self,
        customer_ids: np.ndarray,
        employee_ids: np.ndarray,
        seed: int = 42,
        scale: float = 1.0,
    ):
        super().__init__(config.TICKETS, seed=seed, scale=scale)
        self.customer_ids = customer_ids
        self.employee_ids = employee_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(170)
        srng = self._make_stdlib_rng(170)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        cust_fk = sample_fk_values(self.customer_ids, n, rng=rng)
        # assigned_to: 10% null
        emp_fk = sample_fk_values(self.employee_ids, n, null_pct=0.1, rng=rng)

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)
        updated_ts = ts + rng.integers(0, 86400 * 30, size=n)

        statuses = [srng.choice(_TICKET_STATUSES) for _ in range(n)]
        priorities = [srng.choice(_TICKET_PRIORITIES) for _ in range(n)]
        subjects = [srng.choice(_TICKET_SUBJECTS) for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "customer_id": pa.array(cust_fk[sl].astype(np.int64), type=pa.int64()),
                "assigned_to": make_nullable_int64(emp_fk[sl]),
                "subject": pa.array(subjects[offset:end], type=pa.string()),
                "status": pa.array(statuses[offset:end], type=pa.string()),
                "priority": pa.array(priorities[offset:end], type=pa.string()),
                "created_at": make_timestamps_array(ts[sl]),
                "updated_at": make_timestamps_array(updated_ts[sl]),
            })
            yield batch
            offset = end

    @property
    def ids(self) -> np.ndarray:
        return np.arange(1, self.row_count + 1, dtype=np.int64)


class TicketMessagesGenerator(BaseTableGenerator):
    spec = config.TICKET_MESSAGES

    def __init__(
        self,
        ticket_ids: np.ndarray,
        user_ids: np.ndarray,
        seed: int = 42,
        scale: float = 1.0,
    ):
        super().__init__(config.TICKET_MESSAGES, seed=seed, scale=scale)
        self.ticket_ids = ticket_ids
        self.user_ids = user_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(180)
        srng = self._make_stdlib_rng(180)
        fake = self._make_faker(180)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        ticket_fk = sample_fk_values(self.ticket_ids, n, rng=rng)
        # author_user_id: 5% null
        user_fk = sample_fk_values(self.user_ids, n, null_pct=0.05, rng=rng)

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)

        # Free text with ~6% PII injection
        texts = generate_free_text_batch(n, pii_rate=0.06, rng=srng, fake=fake)

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "ticket_id": pa.array(ticket_fk[sl].astype(np.int64), type=pa.int64()),
                "author_user_id": make_nullable_int64(user_fk[sl]),
                "body": pa.array(texts[offset:end], type=pa.large_utf8()),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class ReviewsGenerator(BaseTableGenerator):
    spec = config.REVIEWS

    def __init__(
        self,
        product_ids: np.ndarray,
        customer_ids: np.ndarray,
        seed: int = 42,
        scale: float = 1.0,
    ):
        super().__init__(config.REVIEWS, seed=seed, scale=scale)
        self.product_ids = product_ids
        self.customer_ids = customer_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(190)
        srng = self._make_stdlib_rng(190)
        fake = self._make_faker(190)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        prod_fk = sample_fk_values(self.product_ids, n, rng=rng)
        cust_fk = sample_fk_values(self.customer_ids, n, rng=rng)

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)

        # Rating: skewed toward 4-5 stars
        ratings = rng.choice([1, 2, 3, 4, 5], size=n,
                              p=[0.05, 0.07, 0.13, 0.30, 0.45])

        is_verified = rng.random(n) > 0.3

        titles = [srng.choice(_REVIEW_TITLES) for _ in range(n)]
        texts = generate_free_text_batch(n, pii_rate=0.06, rng=srng, fake=fake)

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "product_id": pa.array(prod_fk[sl].astype(np.int64), type=pa.int64()),
                "customer_id": pa.array(cust_fk[sl].astype(np.int64), type=pa.int64()),
                "rating": pa.array(ratings[sl], type=pa.int32()),
                "title": pa.array(titles[offset:end], type=pa.string()),
                "body": pa.array(texts[offset:end], type=pa.large_utf8()),
                "is_verified": make_bool_array(is_verified[sl]),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end
