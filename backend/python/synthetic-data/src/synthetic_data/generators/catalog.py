"""Generators for categories, products, inventory, warehouses, warehouse_stock."""

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
)
from synthetic_data.pii.credit_cards import fake_cc_failing_luhn
from synthetic_data.relationships import sample_fk_values

_REF_DT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class CategoriesGenerator(BaseTableGenerator):
    spec = config.CATEGORIES

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.CATEGORIES, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(20)
        srng = self._make_stdlib_rng(20)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int32)

        # First 10 are top-level (null parent), rest reference one of them
        n_top = min(10, n)
        parent_ids = np.empty(n, dtype=np.float64)
        parent_ids[:n_top] = np.nan  # top-level: null parent
        if n > n_top:
            top_ids = ids[:n_top].astype(np.float64)
            # Children reference one of the top-level categories
            for i in range(n_top, n):
                parent_ids[i] = float(srng.choice(top_ids))

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)

        _CATEGORY_NAMES = [
            "Electronics", "Clothing", "Books", "Home & Garden", "Sports",
            "Toys", "Food & Grocery", "Automotive", "Health", "Beauty",
            "Office Supplies", "Pet Supplies", "Music", "Movies", "Gaming",
            "Jewelry", "Tools", "Baby", "Travel", "Art",
            "Outdoor", "Fitness", "Kitchen", "Furniture", "Lighting",
            "Stationery", "Crafts", "Plants", "Cleaning", "Safety",
            "Cameras", "Phones", "Computers", "Tablets", "Audio",
            "Watches", "Bags", "Shoes", "Clothing/Men", "Clothing/Women",
            "Clothing/Kids", "Supplements", "Skincare", "Haircare", "Vitamins",
            "Snacks", "Beverages", "Frozen", "Organic", "Bakery",
        ]
        names = [_CATEGORY_NAMES[i % len(_CATEGORY_NAMES)] for i in range(n)]
        slugs = [name.lower().replace(" ", "-").replace("/", "-").replace("&", "and")
                 for name in names]

        # Descriptions: mostly normal, some with email-shaped noise (rate@5%)
        descriptions = []
        for i in range(n):
            if i % 15 == 0:
                # Noise: email-shaped but invalid
                rate = srng.randint(1, 99)
                descriptions.append(f"Browse our {names[i]} collection. Rating: rate@{rate}%")
            else:
                descriptions.append(f"Browse our wide selection of {names[i]} products.")

        batch = pa.record_batch({
            "id": pa.array(ids, type=pa.int32()),
            "name": pa.array(names, type=pa.string()),
            "slug": pa.array(slugs, type=pa.string()),
            "description": pa.array(descriptions, type=pa.large_utf8()),
            "parent_category_id": make_nullable_int32(parent_ids),
            "created_at": make_timestamps_array(ts),
        })
        yield batch

    @property
    def ids(self) -> np.ndarray:
        return np.arange(1, self.row_count + 1, dtype=np.int32)


class ProductsGenerator(BaseTableGenerator):
    spec = config.PRODUCTS

    def __init__(self, category_ids: np.ndarray, seed: int = 42, scale: float = 1.0):
        super().__init__(config.PRODUCTS, seed=seed, scale=scale)
        self.category_ids = category_ids
        # Populated as a side-effect of `batches()` so downstream generators
        # (order_items, payments) can reference the *exact* prices written
        # to products.parquet — see runner.py.
        self.prices: np.ndarray | None = None

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(30)
        srng = self._make_stdlib_rng(30)
        fake = self._make_faker(30)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)

        # FK: category_id — uniform distribution
        cat_fk = sample_fk_values(self.category_ids.astype(np.int64), n, rng=rng)

        # Prices: lognormal, mean ~$50, range $5-$5000
        prices = lognormal_float(mean=3.9, sigma=1.0, size=n, lo=5.0, hi=5000.0, rng=rng)
        # Expose for downstream generators (order_items.unit_price, payments.amount).
        self.prices = prices

        # is_active: 92% active
        is_active = rng.random(n) > 0.08

        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)

        _PREFIXES = ["Pro", "Ultra", "Super", "Mega", "Basic", "Premium", "Classic", "Advanced"]
        _NOUNS = ["Widget", "Gadget", "Device", "Tool", "Kit", "Set", "Pack", "Bundle",
                  "System", "Unit", "Module", "Component", "Adapter", "Cable", "Charger"]

        names = [f"{srng.choice(_PREFIXES)} {srng.choice(_NOUNS)} {i + 1}" for i in range(n)]
        skus = [f"SKU-{i + 1:06d}" for i in range(n)]
        descriptions = [fake.sentence(nb_words=10) for _ in range(n)]

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)

            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "sku": pa.array(skus[offset:end], type=pa.string()),
                "name": pa.array(names[offset:end], type=pa.string()),
                "description": pa.array(descriptions[offset:end], type=pa.large_utf8()),
                "category_id": pa.array(cat_fk[sl].astype(np.int32), type=pa.int32()),
                "price": pa.array(prices[sl], type=pa.float64()),
                "is_active": make_bool_array(is_active[sl]),
                "created_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end

    @property
    def ids(self) -> np.ndarray:
        return np.arange(1, self.row_count + 1, dtype=np.int64)


class WarehousesGenerator(BaseTableGenerator):
    spec = config.WAREHOUSES

    def __init__(self, seed: int = 42, scale: float = 1.0):
        super().__init__(config.WAREHOUSES, seed=seed, scale=scale)

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(40)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int32)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 3, rng=rng)
        is_active = rng.random(n) > 0.1

        _CITIES = [
            ("New York", "US"), ("Los Angeles", "US"), ("Chicago", "US"),
            ("Houston", "US"), ("Phoenix", "US"), ("Philadelphia", "US"),
            ("San Antonio", "US"), ("San Diego", "US"), ("Dallas", "US"),
            ("San Jose", "US"), ("London", "GB"), ("Manchester", "GB"),
            ("Berlin", "DE"), ("Munich", "DE"), ("Paris", "FR"),
            ("Lyon", "FR"), ("Toronto", "CA"), ("Vancouver", "CA"),
            ("Sydney", "AU"), ("Melbourne", "AU"),
        ]
        names = [f"Warehouse {city}" for city, _ in _CITIES[:n]]
        cities = [city for city, _ in _CITIES[:n]]
        countries = [cc for _, cc in _CITIES[:n]]

        batch = pa.record_batch({
            "id": pa.array(ids, type=pa.int32()),
            "name": pa.array(names, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "country_code": pa.array(countries, type=pa.string()),
            "is_active": make_bool_array(is_active),
            "created_at": make_timestamps_array(ts),
        })
        yield batch

    @property
    def ids(self) -> np.ndarray:
        return np.arange(1, self.row_count + 1, dtype=np.int32)


class InventoryGenerator(BaseTableGenerator):
    spec = config.INVENTORY

    def __init__(self, product_ids: np.ndarray, seed: int = 42, scale: float = 1.0):
        super().__init__(config.INVENTORY, seed=seed, scale=scale)
        self.product_ids = product_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(50)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)

        # FK: one inventory row per product (one_to_one)
        prod_fk = sample_fk_values(
            self.product_ids[:n].astype(np.int64), n,
            cardinality="one_to_one", rng=rng
        )

        qty_on_hand = rng.integers(0, 10_000, size=n, dtype=np.int64)
        qty_reserved = rng.integers(0, qty_on_hand.clip(min=1), size=n, dtype=np.int64)
        reorder_level = rng.integers(10, 500, size=n, dtype=np.int64)

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "product_id": pa.array(prod_fk[sl], type=pa.int64()),
                "quantity_on_hand": pa.array(qty_on_hand[sl], type=pa.int64()),
                "quantity_reserved": pa.array(qty_reserved[sl], type=pa.int64()),
                "reorder_level": pa.array(reorder_level[sl], type=pa.int64()),
                "updated_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end


class WarehouseStockGenerator(BaseTableGenerator):
    spec = config.WAREHOUSE_STOCK

    def __init__(
        self,
        product_ids: np.ndarray,
        warehouse_ids: np.ndarray,
        seed: int = 42,
        scale: float = 1.0,
    ):
        super().__init__(config.WAREHOUSE_STOCK, seed=seed, scale=scale)
        self.product_ids = product_ids
        self.warehouse_ids = warehouse_ids

    def batches(self) -> Iterator[pa.RecordBatch]:
        rng = self._make_rng(60)
        srng = self._make_stdlib_rng(60)
        n = self.row_count

        ids = np.arange(1, n + 1, dtype=np.int64)
        ts = uniform_timestamps(_REF_DT, n, span_days=365 * 2, rng=rng)

        prod_fk = sample_fk_values(self.product_ids.astype(np.int64), n, rng=rng)
        wh_fk = sample_fk_values(self.warehouse_ids.astype(np.int64), n, rng=rng)
        qty = rng.integers(0, 5_000, size=n, dtype=np.int64)

        # Tracking numbers that LOOK like CC but fail Luhn
        tracking_nums = fake_cc_failing_luhn(n, rng=srng)

        offset = 0
        while offset < n:
            end = min(offset + self.BATCH_SIZE, n)
            sl = slice(offset, end)
            batch = pa.record_batch({
                "id": pa.array(ids[sl], type=pa.int64()),
                "product_id": pa.array(prod_fk[sl], type=pa.int64()),
                "warehouse_id": pa.array(wh_fk[sl].astype(np.int32), type=pa.int32()),
                "quantity": pa.array(qty[sl], type=pa.int64()),
                "tracking_number": pa.array(tracking_nums[offset:end], type=pa.string()),
                "updated_at": make_timestamps_array(ts[sl]),
            })
            yield batch
            offset = end
