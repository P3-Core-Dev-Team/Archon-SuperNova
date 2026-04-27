"""Generators for orders, order_items, and payments tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import numpy as np
import pyarrow as pa

from synthetic_data import config
from synthetic_data.distributions import (
    business_hours_timestamps,
    lognormal_int,
    lognormal_float,
    uniform_timestamps,
)
from synthetic_data.generators.base import (
    BaseTableGenerator,
    make_timestamps_array,
    make_uuid_array,
    make_nullable_int64,
    make_bool_array,
)
from synthetic_data.pii.credit_cards import generate_luhn_batch
from synthetic_data.pii.iban import generate_iban_batch
from synthetic_data.relationships import sample_fk_values

_REF_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_STATUSES_ORDER = ["pending", "processing", "shipped", "delivered", "cancelled", "refunded"]
_STATUSES_PAY = ["completed", "pending", "failed", "refunded"]
_CURRENCIES = ["USD"] * 80 + ["EUR"] * 10 + ["GBP"] * 5 + ["CAD"] * 3 + ["AUD"] * 2


class OrdersGenerator(BaseTableGenerator):
    spec = config.ORDERS

    def __init__(
        self,
        customer_ids: np.ndarray,
        address_ids: np.ndarray,
        seed: int = 42,
        scale: float = 1.0,
    ):
        super().__init__(config.ORDERS, seed=seed, scale=scale)
        self.customer_ids = customer_ids
        self.address_ids = address_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(70)
        srng = self._make_stdlib_rng(70)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)

        # Zipfian customer_id: top 5% of customers = ~50% of orders
        cust_fk = sample_fk_values(
            self.customer_ids, n,
            zipfian_s=1.5, rng=rng
        )

        # Uniform address_id
        addr_fk = sample_fk_values(self.address_ids, n, rng=rng)

        # Timestamps: business hours over 3 years
        ts = business_hours_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)
        updated_ts = ts + rng.integers(0, 86400 * 14, size=n)

        # Total amounts will be set by payments (not used here, just placeholder)
        total_amounts = lognormal_float(mean=4.0, sigma=1.2, size=n, lo=1.0, hi=50_000.0, rng=rng)

        statuses = [srng.choice(_STATUSES_ORDER) for _ in range(n)]
        currencies = [srng.choice(_CURRENCIES) for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "customer_id": pa.array(cust_fk[sl].astype(np.int64), type=pa.int64()),
                "shipping_address_id": pa.array(addr_fk[sl].astype(np.int64), type=pa.int64()),
                "status": pa.array(statuses[offset:end], type=pa.string()),
                "currency": pa.array(currencies[offset:end], type=pa.string()),
                "total_amount": pa.array(total_amounts[sl], type=pa.float64()),
                "created_at": make_timestamps_array(ts[sl]),
                "updated_at": make_timestamps_array(updated_ts[sl]),
            })
            yield batch
            offset = end

    @property
    def ids(self) -> np.ndarray:
        return np.arange(1, self.row_count + 1, dtype=np.int64)


class OrderItemsGenerator(BaseTableGenerator):
    spec = config.ORDER_ITEMS

    def __init__(
        self,
        order_ids: np.ndarray,
        product_ids: np.ndarray,
        product_prices: np.ndarray,
        seed: int = 42,
        scale: float = 1.0,
    ):
        super().__init__(config.ORDER_ITEMS, seed=seed, scale=scale)
        self.order_ids = order_ids
        self.product_ids = product_ids
        self.product_prices = product_prices  # parallel with product_ids
        # Populated as a side-effect of `batches()` — accumulated per-order
        # subtotal so PaymentsGenerator can stamp matching `amount` values
        # without re-running the full row generation.
        self.order_subtotals: dict[int, float] = {}

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(80)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)

        # Zipfian product_id: top 20% products = ~80% of order items (s=1.8)
        prod_idx = sample_fk_values(
            self.product_ids, n,
            zipfian_s=1.8, rng=rng
        )
        # Prices from product catalog
        unit_prices = self.product_prices[prod_idx.astype(np.int64) - 1]  # product IDs start at 1

        # Uniform order_id FK
        order_fk = sample_fk_values(self.order_ids, n, rng=rng)

        # Quantity: lognormal, mostly 1-3, some 10-50
        quantities = lognormal_int(mean=0.5, sigma=0.8, size=n, lo=1, hi=50, rng=rng)

        # Discount: 0-30%, mostly 0
        discounts = np.where(rng.random(n) < 0.25, rng.random(n) * 30, 0.0)

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)

        # Reset and accumulate streaming subtotals during emission so payments
        # can read `self.order_subtotals` without re-generating the table.
        self.order_subtotals = {}

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)

            # Accumulate per-order subtotal from this batch's slice only.
            batch_line_totals = (
                unit_prices[sl] * quantities[sl] * (1 - discounts[sl] / 100.0)
            )
            batch_order_ids = order_fk[sl].astype(np.int64)
            for oid, lt in zip(batch_order_ids.tolist(), batch_line_totals.tolist()):
                self.order_subtotals[oid] = self.order_subtotals.get(oid, 0.0) + lt

            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "order_id": pa.array(order_fk[sl].astype(np.int64), type=pa.int64()),
                "product_id": pa.array(prod_idx[sl].astype(np.int64), type=pa.int64()),
                "quantity": pa.array(quantities[sl], type=pa.int64()),
                "unit_price": pa.array(unit_prices[sl], type=pa.float64()),
                "discount_pct": pa.array(discounts[sl], type=pa.float64()),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class PaymentsGenerator(BaseTableGenerator):
    spec = config.PAYMENTS

    def __init__(
        self,
        order_ids: np.ndarray,
        customer_ids: np.ndarray,
        order_subtotals: dict[int, float],
        seed: int = 42,
        scale: float = 1.0,
    ):
        super().__init__(config.PAYMENTS, seed=seed, scale=scale)
        self.order_ids = order_ids
        self.customer_ids = customer_ids
        self.order_subtotals = order_subtotals

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(90)
        srng = self._make_stdlib_rng(90)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)

        # 1:1 payment per order
        order_fk = sample_fk_values(
            self.order_ids, n,
            cardinality="one_to_one" if n <= len(self.order_ids) else "many_to_one",
            rng=rng
        )
        cust_fk = sample_fk_values(self.customer_ids, n, rng=rng)

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)

        # Amounts: match order subtotals where available
        amounts = np.array([
            self.order_subtotals.get(int(oid), float(srng.uniform(10, 500)))
            for oid in order_fk.astype(np.int64).tolist()
        ], dtype=np.float64)
        # Clamp to reasonable range
        amounts = np.clip(amounts, 0.01, 1_000_000.0)

        # PII
        cc_raw = generate_luhn_batch(n, rng=srng)
        cc_last4 = [cc[-4:] for cc in cc_raw]
        ibans = generate_iban_batch(n, rng=srng)
        statuses = [srng.choice(_STATUSES_PAY) for _ in range(n)]
        currencies = [srng.choice(_CURRENCIES) for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "transaction_id": make_uuid_array(end - offset, rng),
                "order_id": pa.array(order_fk[sl].astype(np.int64), type=pa.int64()),
                "customer_id": pa.array(cust_fk[sl].astype(np.int64), type=pa.int64()),
                "amount": pa.array(amounts[sl], type=pa.float64()),
                "currency": pa.array(currencies[offset:end], type=pa.string()),
                "card_number_last4": pa.array(cc_last4[offset:end], type=pa.string()),
                "card_number_raw": pa.array(cc_raw[offset:end], type=pa.string()),
                "iban": pa.array(ibans[offset:end], type=pa.string()),
                "status": pa.array(statuses[offset:end], type=pa.string()),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end
