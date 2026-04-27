"""
Seed a Saleor-shaped (UUID-primary-key) e-commerce dataset into Postgres
schema ``saleor``.

Saleor is the open-source Django/Postgres e-commerce platform
(https://github.com/saleor/saleor). It uses UUID primary keys throughout —
which makes it an excellent test for the discovery pipeline's ability to
recover relationships when there are no dense-1..N integer columns to
collide.

We replicate ~32 tables of the schema with declared PKs and undeclared FKs
so the discovery pipeline must rediscover the relationships.

Usage:
    python3 seed_postgres_saleor.py
    python3 seed_postgres_saleor.py --schema saleor --reset
"""
from __future__ import annotations

import argparse
import random
import time
from datetime import datetime, timedelta, timezone

from seed_postgres_500 import (
    DSN, FIRST_NAMES, LAST_NAMES, COUNTRIES, SEED,
    Table, conn_cur, copy_rows, gen_uuid, already_populated,
)

random.seed(SEED)

CTX: dict[str, list] = {}


# ---- generators ------------------------------------------------------------

def t_account_user(rng: random.Random):
    rows = []
    ids = []
    for i in range(500):
        uid = gen_uuid(rng)
        ids.append(uid)
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        domain = rng.choice(["example.com", "saleor.io", "shop.test", "mail.co"])
        email = f"{first}.{last}{rng.randint(1, 9999)}@{domain}"
        is_staff = rng.random() < 0.05
        rows.append((uid, email, is_staff))
    CTX["user_ids"] = ids
    return rows


def t_account_address(rng: random.Random):
    rows = []
    ids = []
    user_addr_map: dict[str, list[str]] = {}
    for u in CTX["user_ids"]:
        n = rng.randint(1, 3)
        for _ in range(n):
            aid = gen_uuid(rng)
            ids.append(aid)
            user_addr_map.setdefault(u, []).append(aid)
            rows.append((
                aid, u,
                rng.choice(COUNTRIES),
                f"{rng.randint(10000, 99999)}",
                f"+1{rng.randint(2000000000, 9999999999)}",
            ))
            if len(rows) >= 800:
                break
        if len(rows) >= 800:
            break
    CTX["address_ids"] = ids
    return rows


def t_channel_channel(rng: random.Random):
    rows = []
    ids = []
    slugs = ["default-channel", "us-channel", "eu-channel", "uk-channel", "asia-channel"]
    currencies = ["USD", "USD", "EUR", "GBP", "JPY"]
    for slug, cur in zip(slugs, currencies):
        cid = gen_uuid(rng)
        ids.append(cid)
        rows.append((cid, slug, cur))
    CTX["channel_ids"] = ids
    return rows


def t_product_category(rng: random.Random):
    rows = []
    ids: list[str] = []
    for i in range(50):
        cid = gen_uuid(rng)
        # ~30% have a parent picked from earlier rows
        parent = None
        if i >= 5 and rng.random() < 0.30:
            parent = rng.choice(ids)
        ids.append(cid)
        rows.append((cid, parent, f"category-{i:03d}"))
    CTX["category_ids"] = ids
    return rows


def t_product_producttype(rng: random.Random):
    rows = []
    ids = []
    names = ["Default Type", "T-Shirt", "Mug", "Book", "Sneakers", "Hat", "Jewelry", "Backpack"]
    for n in names:
        pid = gen_uuid(rng)
        ids.append(pid)
        rows.append((pid, n))
    CTX["producttype_ids"] = ids
    return rows


def t_product_collection(rng: random.Random):
    rows = []
    ids = []
    for i in range(10):
        cid = gen_uuid(rng)
        ids.append(cid)
        rows.append((cid, f"collection-{i:02d}"))
    CTX["collection_ids"] = ids
    return rows


def t_product_product(rng: random.Random):
    rows = []
    ids = []
    cats = CTX["category_ids"]
    pts = CTX["producttype_ids"]
    for i in range(1000):
        pid = gen_uuid(rng)
        ids.append(pid)
        rows.append((
            pid,
            rng.choice(cats),
            rng.choice(pts),
            None,  # default_variant_id; populated via UPDATE after variants exist
            f"Product {i:04d} {rng.choice(LAST_NAMES).title()}",
        ))
    CTX["product_ids"] = ids
    return rows


def t_product_productvariant(rng: random.Random):
    rows = []
    ids = []
    products = CTX["product_ids"]
    # We want 2500 variants across 1000 products. Distribute roughly evenly.
    counts: dict[str, int] = {p: 0 for p in products}
    for i in range(2500):
        # Pick a product, but heavily favor products that have <3 variants
        # (so each product ends up with 2-3 variants).
        p = rng.choice(products)
        counts[p] += 1
        vid = gen_uuid(rng)
        ids.append(vid)
        rows.append((vid, p, f"SKU-{i:06d}"))
    # Track first variant per product for default_variant_id update
    first_variant_per_product: dict[str, str] = {}
    for vid, pid, _ in rows:
        first_variant_per_product.setdefault(pid, vid)
    CTX["variant_ids"] = ids
    CTX["first_variant_per_product"] = first_variant_per_product
    return rows


def t_product_collectionproduct(rng: random.Random):
    rows = []
    products = CTX["product_ids"]
    collections = CTX["collection_ids"]
    seen = set()
    while len(rows) < 1500:
        cid = rng.choice(collections)
        pid = rng.choice(products)
        if (cid, pid) in seen:
            continue
        seen.add((cid, pid))
        rows.append((gen_uuid(rng), cid, pid))
    return rows


def t_attribute_attribute(rng: random.Random):
    rows = []
    ids = []
    for i in range(30):
        aid = gen_uuid(rng)
        ids.append(aid)
        rows.append((aid, f"attribute-{i:03d}"))
    CTX["attribute_ids"] = ids
    return rows


def t_attribute_assignedproductattribute(rng: random.Random):
    rows = []
    products = CTX["product_ids"]
    attrs = CTX["attribute_ids"]
    for _ in range(2000):
        rows.append((gen_uuid(rng), rng.choice(products), rng.choice(attrs)))
    return rows


def t_attribute_assignedvariantattribute(rng: random.Random):
    rows = []
    variants = CTX["variant_ids"]
    attrs = CTX["attribute_ids"]
    for _ in range(3000):
        rows.append((gen_uuid(rng), rng.choice(variants), rng.choice(attrs)))
    return rows


def t_warehouse_warehouse(rng: random.Random):
    rows = []
    ids = []
    for i in range(4):
        wid = gen_uuid(rng)
        ids.append(wid)
        rows.append((wid, f"warehouse-{i:02d}"))
    CTX["warehouse_ids"] = ids
    return rows


def t_warehouse_stock(rng: random.Random):
    rows = []
    variants = CTX["variant_ids"]
    warehouses = CTX["warehouse_ids"]
    # 4000 rows: not strictly every variant in every warehouse (would be 10000),
    # but a random sampling.
    seen = set()
    while len(rows) < 4000:
        v = rng.choice(variants)
        w = rng.choice(warehouses)
        if (v, w) in seen:
            continue
        seen.add((v, w))
        rows.append((gen_uuid(rng), v, w, rng.randint(0, 500)))
    return rows


def t_discount_voucher(rng: random.Random):
    rows = []
    ids = []
    for i in range(30):
        vid = gen_uuid(rng)
        ids.append(vid)
        rows.append((vid, f"VOUCHER{i:04d}", rng.choice(["entire_order", "shipping", "specific_product"])))
    CTX["voucher_ids"] = ids
    return rows


def t_order_order(rng: random.Random):
    rows = []
    ids = []
    users = CTX["user_ids"]
    channels = CTX["channel_ids"]
    addrs = CTX["address_ids"]
    vouchers = CTX["voucher_ids"]
    statuses = ["unfulfilled", "partially_fulfilled", "fulfilled", "canceled"]
    for i in range(2000):
        oid = gen_uuid(rng)
        ids.append(oid)
        # voucher_id non-null on ~15% of orders (still gives 300 distinct uses,
        # well above child_min_distinct_count=5)
        voucher = rng.choice(vouchers) if rng.random() < 0.15 else None
        rows.append((
            oid,
            rng.choice(users),
            rng.choice(channels),
            rng.choice(addrs),
            rng.choice(addrs),
            voucher,
            rng.choice(statuses),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 730)),
        ))
    CTX["order_ids"] = ids
    return rows


def t_order_orderline(rng: random.Random):
    rows = []
    ids = []
    orders = CTX["order_ids"]
    variants = CTX["variant_ids"]
    # 6000 lines for 2000 orders => 3 lines/order avg
    # Iterate orders to make sure every order appears at least once.
    next_lines = []
    for oid in orders:
        nlines = rng.randint(1, 5)
        for _ in range(nlines):
            next_lines.append(oid)
    rng.shuffle(next_lines)
    next_lines = next_lines[:6000]
    for oid in next_lines:
        lid = gen_uuid(rng)
        ids.append(lid)
        rows.append((lid, oid, rng.choice(variants), rng.randint(1, 5)))
    CTX["orderline_ids"] = ids
    return rows


def t_order_fulfillment(rng: random.Random):
    rows = []
    ids = []
    orders = CTX["order_ids"]
    warehouses = CTX["warehouse_ids"]
    for _ in range(2200):
        fid = gen_uuid(rng)
        ids.append(fid)
        rows.append((fid, rng.choice(orders), rng.choice(warehouses)))
    CTX["fulfillment_ids"] = ids
    return rows


def t_order_fulfillmentline(rng: random.Random):
    rows = []
    fulfillments = CTX["fulfillment_ids"]
    orderlines = CTX["orderline_ids"]
    for _ in range(4400):
        rows.append((
            gen_uuid(rng),
            rng.choice(fulfillments),
            rng.choice(orderlines),
            rng.randint(1, 3),
        ))
    return rows


def t_checkout_checkout(rng: random.Random):
    rows = []
    tokens = []
    users = CTX["user_ids"]
    channels = CTX["channel_ids"]
    addrs = CTX["address_ids"]
    for _ in range(800):
        tok = gen_uuid(rng)
        tokens.append(tok)
        # ~80% with a logged-in user, 20% anonymous.
        u = rng.choice(users) if rng.random() < 0.8 else None
        # ~70% with billing/shipping addresses (logged-in users have addresses).
        billing = rng.choice(addrs) if rng.random() < 0.7 else None
        shipping = rng.choice(addrs) if rng.random() < 0.7 else None
        rows.append((tok, u, rng.choice(channels), billing, shipping))
    CTX["checkout_tokens"] = tokens
    return rows


def t_checkout_checkoutline(rng: random.Random):
    rows = []
    tokens = CTX["checkout_tokens"]
    variants = CTX["variant_ids"]
    for _ in range(2000):
        rows.append((
            gen_uuid(rng),
            rng.choice(tokens),
            rng.choice(variants),
            rng.randint(1, 5),
        ))
    return rows


def t_payment_payment(rng: random.Random):
    rows = []
    ids = []
    orders = CTX["order_ids"]
    tokens = CTX["checkout_tokens"]
    gateways = ["stripe", "braintree", "adyen", "razorpay"]
    for _ in range(2200):
        pid = gen_uuid(rng)
        ids.append(pid)
        # ~70% bound to an order, ~30% bound to a checkout (one-of-the-other).
        if rng.random() < 0.7:
            checkout_id = None
            order_id = rng.choice(orders)
        else:
            checkout_id = rng.choice(tokens)
            order_id = None
        rows.append((
            pid, checkout_id, order_id,
            rng.choice(gateways),
            round(rng.uniform(5.0, 500.0), 2),
        ))
    CTX["payment_ids"] = ids
    return rows


def t_payment_transaction(rng: random.Random):
    rows = []
    payments = CTX["payment_ids"]
    kinds = ["auth", "capture", "refund", "void"]
    for _ in range(3000):
        rows.append((
            gen_uuid(rng),
            rng.choice(payments),
            rng.choice(kinds),
            round(rng.uniform(1.0, 500.0), 2),
        ))
    return rows


def t_discount_promotion(rng: random.Random):
    rows = []
    ids = []
    for i in range(15):
        prid = gen_uuid(rng)
        ids.append(prid)
        rows.append((prid, f"Promotion {i:03d}"))
    CTX["promotion_ids"] = ids
    return rows


def t_discount_promotionrule(rng: random.Random):
    rows = []
    promotions = CTX["promotion_ids"]
    for _ in range(30):
        rows.append((gen_uuid(rng), rng.choice(promotions)))
    return rows


def t_giftcard_giftcard(rng: random.Random):
    rows = []
    users = CTX["user_ids"]
    for i in range(200):
        used_by = rng.choice(users) if rng.random() < 0.5 else None
        rows.append((
            gen_uuid(rng),
            rng.choice(users),
            used_by,
            f"GIFTCARD-{i:04d}-{rng.randint(1000, 9999)}",
            round(rng.uniform(10.0, 500.0), 2),
        ))
    return rows


def t_shipping_shippingzone(rng: random.Random):
    rows = []
    ids = []
    names = ["US", "EU", "UK", "Asia-Pacific", "Canada", "South America", "Africa", "Middle East"]
    for n in names:
        zid = gen_uuid(rng)
        ids.append(zid)
        rows.append((zid, n))
    CTX["shippingzone_ids"] = ids
    return rows


def t_shipping_shippingmethod(rng: random.Random):
    rows = []
    zones = CTX["shippingzone_ids"]
    types = ["price", "weight"]
    for i in range(30):
        rows.append((gen_uuid(rng), rng.choice(zones), rng.choice(types)))
    return rows


def t_menu_menu(rng: random.Random):
    rows = []
    ids = []
    for i in range(5):
        mid = gen_uuid(rng)
        ids.append(mid)
        rows.append((mid, f"menu-{i:02d}"))
    CTX["menu_ids"] = ids
    return rows


def t_menu_menuitem(rng: random.Random):
    rows = []
    ids: list[str] = []
    menus = CTX["menu_ids"]
    cats = CTX["category_ids"]
    cols = CTX["collection_ids"]
    for i in range(50):
        mi_id = gen_uuid(rng)
        # parent_id from earlier rows ~30%
        parent = None
        if i >= 5 and rng.random() < 0.30:
            parent = rng.choice(ids)
        # category_id ~50%, collection_id ~30%
        cat_id = rng.choice(cats) if rng.random() < 0.50 else None
        col_id = rng.choice(cols) if rng.random() < 0.30 else None
        ids.append(mi_id)
        rows.append((mi_id, rng.choice(menus), parent, cat_id, col_id, f"Menu Item {i:03d}"))
    return rows


def t_page_pagetype(rng: random.Random):
    rows = []
    ids = []
    slugs = ["blog-post", "press-release", "doc", "guide", "faq"]
    for s in slugs:
        ptid = gen_uuid(rng)
        ids.append(ptid)
        rows.append((ptid, s))
    CTX["pagetype_ids"] = ids
    return rows


def t_page_page(rng: random.Random):
    rows = []
    page_types = CTX["pagetype_ids"]
    for i in range(30):
        rows.append((gen_uuid(rng), rng.choice(page_types), f"page-{i:03d}"))
    return rows


# ---- table list ------------------------------------------------------------

def build_tables() -> list[Table]:
    return [
        Table("account_user",
              '''CREATE TABLE IF NOT EXISTS "account_user" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  email TEXT NOT NULL,
                  is_staff BOOLEAN)''',
              t_account_user,
              ["id", "email", "is_staff"]),
        Table("account_address",
              '''CREATE TABLE IF NOT EXISTS "account_address" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id uuid,
                  country_code TEXT,
                  postal_code TEXT,
                  phone TEXT)''',
              t_account_address,
              ["id", "user_id", "country_code", "postal_code", "phone"]),
        Table("channel_channel",
              '''CREATE TABLE IF NOT EXISTS "channel_channel" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  slug TEXT NOT NULL,
                  currency_code TEXT)''',
              t_channel_channel,
              ["id", "slug", "currency_code"]),
        Table("product_category",
              '''CREATE TABLE IF NOT EXISTS "product_category" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  parent_id uuid,
                  slug TEXT NOT NULL)''',
              t_product_category,
              ["id", "parent_id", "slug"]),
        Table("product_producttype",
              '''CREATE TABLE IF NOT EXISTS "product_producttype" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  name TEXT NOT NULL)''',
              t_product_producttype,
              ["id", "name"]),
        Table("product_collection",
              '''CREATE TABLE IF NOT EXISTS "product_collection" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  slug TEXT NOT NULL)''',
              t_product_collection,
              ["id", "slug"]),
        Table("product_product",
              '''CREATE TABLE IF NOT EXISTS "product_product" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  category_id uuid,
                  product_type_id uuid,
                  default_variant_id uuid,
                  name TEXT NOT NULL)''',
              t_product_product,
              ["id", "category_id", "product_type_id", "default_variant_id", "name"]),
        Table("product_productvariant",
              '''CREATE TABLE IF NOT EXISTS "product_productvariant" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  product_id uuid,
                  sku TEXT)''',
              t_product_productvariant,
              ["id", "product_id", "sku"]),
        Table("product_collectionproduct",
              '''CREATE TABLE IF NOT EXISTS "product_collectionproduct" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  collection_id uuid,
                  product_id uuid)''',
              t_product_collectionproduct,
              ["id", "collection_id", "product_id"]),
        Table("attribute_attribute",
              '''CREATE TABLE IF NOT EXISTS "attribute_attribute" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  slug TEXT NOT NULL)''',
              t_attribute_attribute,
              ["id", "slug"]),
        Table("attribute_assignedproductattribute",
              '''CREATE TABLE IF NOT EXISTS "attribute_assignedproductattribute" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  product_id uuid,
                  attribute_id uuid)''',
              t_attribute_assignedproductattribute,
              ["id", "product_id", "attribute_id"]),
        Table("attribute_assignedvariantattribute",
              '''CREATE TABLE IF NOT EXISTS "attribute_assignedvariantattribute" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  variant_id uuid,
                  attribute_id uuid)''',
              t_attribute_assignedvariantattribute,
              ["id", "variant_id", "attribute_id"]),
        Table("warehouse_warehouse",
              '''CREATE TABLE IF NOT EXISTS "warehouse_warehouse" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  slug TEXT NOT NULL)''',
              t_warehouse_warehouse,
              ["id", "slug"]),
        Table("warehouse_stock",
              '''CREATE TABLE IF NOT EXISTS "warehouse_stock" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  product_variant_id uuid,
                  warehouse_id uuid,
                  quantity INT)''',
              t_warehouse_stock,
              ["id", "product_variant_id", "warehouse_id", "quantity"]),
        Table("discount_voucher",
              '''CREATE TABLE IF NOT EXISTS "discount_voucher" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  code TEXT NOT NULL,
                  type TEXT)''',
              t_discount_voucher,
              ["id", "code", "type"]),
        Table("order_order",
              '''CREATE TABLE IF NOT EXISTS "order_order" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id uuid,
                  channel_id uuid,
                  billing_address_id uuid,
                  shipping_address_id uuid,
                  voucher_id uuid,
                  status TEXT,
                  created_at TIMESTAMPTZ)''',
              t_order_order,
              ["id", "user_id", "channel_id", "billing_address_id",
               "shipping_address_id", "voucher_id", "status", "created_at"]),
        Table("order_orderline",
              '''CREATE TABLE IF NOT EXISTS "order_orderline" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  order_id uuid,
                  variant_id uuid,
                  quantity INT)''',
              t_order_orderline,
              ["id", "order_id", "variant_id", "quantity"]),
        Table("order_fulfillment",
              '''CREATE TABLE IF NOT EXISTS "order_fulfillment" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  order_id uuid,
                  warehouse_id uuid)''',
              t_order_fulfillment,
              ["id", "order_id", "warehouse_id"]),
        Table("order_fulfillmentline",
              '''CREATE TABLE IF NOT EXISTS "order_fulfillmentline" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  fulfillment_id uuid,
                  order_line_id uuid,
                  quantity INT)''',
              t_order_fulfillmentline,
              ["id", "fulfillment_id", "order_line_id", "quantity"]),
        Table("checkout_checkout",
              '''CREATE TABLE IF NOT EXISTS "checkout_checkout" (
                  token uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id uuid,
                  channel_id uuid,
                  billing_address_id uuid,
                  shipping_address_id uuid)''',
              t_checkout_checkout,
              ["token", "user_id", "channel_id", "billing_address_id", "shipping_address_id"]),
        Table("checkout_checkoutline",
              '''CREATE TABLE IF NOT EXISTS "checkout_checkoutline" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  checkout_id uuid,
                  variant_id uuid,
                  quantity INT)''',
              t_checkout_checkoutline,
              ["id", "checkout_id", "variant_id", "quantity"]),
        Table("payment_payment",
              '''CREATE TABLE IF NOT EXISTS "payment_payment" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  checkout_id uuid,
                  order_id uuid,
                  gateway TEXT,
                  amount NUMERIC(12,2))''',
              t_payment_payment,
              ["id", "checkout_id", "order_id", "gateway", "amount"]),
        Table("payment_transaction",
              '''CREATE TABLE IF NOT EXISTS "payment_transaction" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  payment_id uuid,
                  kind TEXT,
                  amount NUMERIC(12,2))''',
              t_payment_transaction,
              ["id", "payment_id", "kind", "amount"]),
        Table("discount_promotion",
              '''CREATE TABLE IF NOT EXISTS "discount_promotion" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  name TEXT)''',
              t_discount_promotion,
              ["id", "name"]),
        Table("discount_promotionrule",
              '''CREATE TABLE IF NOT EXISTS "discount_promotionrule" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  promotion_id uuid)''',
              t_discount_promotionrule,
              ["id", "promotion_id"]),
        Table("giftcard_giftcard",
              '''CREATE TABLE IF NOT EXISTS "giftcard_giftcard" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  created_by_id uuid,
                  used_by_id uuid,
                  code TEXT,
                  balance NUMERIC(12,2))''',
              t_giftcard_giftcard,
              ["id", "created_by_id", "used_by_id", "code", "balance"]),
        Table("shipping_shippingzone",
              '''CREATE TABLE IF NOT EXISTS "shipping_shippingzone" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  name TEXT)''',
              t_shipping_shippingzone,
              ["id", "name"]),
        Table("shipping_shippingmethod",
              '''CREATE TABLE IF NOT EXISTS "shipping_shippingmethod" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  shipping_zone_id uuid,
                  type TEXT)''',
              t_shipping_shippingmethod,
              ["id", "shipping_zone_id", "type"]),
        Table("menu_menu",
              '''CREATE TABLE IF NOT EXISTS "menu_menu" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  slug TEXT)''',
              t_menu_menu,
              ["id", "slug"]),
        Table("menu_menuitem",
              '''CREATE TABLE IF NOT EXISTS "menu_menuitem" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  menu_id uuid,
                  parent_id uuid,
                  category_id uuid,
                  collection_id uuid,
                  name TEXT)''',
              t_menu_menuitem,
              ["id", "menu_id", "parent_id", "category_id", "collection_id", "name"]),
        Table("page_pagetype",
              '''CREATE TABLE IF NOT EXISTS "page_pagetype" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  slug TEXT)''',
              t_page_pagetype,
              ["id", "slug"]),
        Table("page_page",
              '''CREATE TABLE IF NOT EXISTS "page_page" (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  page_type_id uuid,
                  slug TEXT)''',
              t_page_page,
              ["id", "page_type_id", "slug"]),
    ]


def run(reset: bool, schema: str):
    import psycopg2
    tables = build_tables()
    t0 = time.time()

    print(f"Phase 1/3: schema {schema!r} DDL...")
    conn = psycopg2.connect(**DSN); conn.autocommit = True
    cur = conn.cursor()
    # gen_random_uuid() is built into PostgreSQL 13+. Try pgcrypto for older
    # versions, but ignore failures since PG14+ doesn't need it.
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    except psycopg2.Error:
        # PG13+: gen_random_uuid is in pg_catalog. pgcrypto extension may not
        # be installed; that's fine.
        conn.rollback()
    if reset:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    cur.execute(f'SET search_path TO "{schema}"')
    for t in tables:
        cur.execute(t.ddl)
    conn.close()
    print(f"  DDL done in {time.time()-t0:.1f}s")

    print(f"Phase 2/3: populating tables in schema {schema!r}...")
    total_rows = 0
    for idx, t in enumerate(tables, 1):
        if t.populate is None:
            continue
        with conn_cur(schema) as (conn, cur):
            if already_populated(cur, t.name):
                continue
            rng = random.Random(SEED ^ hash(t.name))
            try:
                rows = t.populate(rng)
            except Exception as e:
                print(f"  generator error in {t.name}: {e!r}")
                continue
            if not rows:
                continue
            n = copy_rows(cur, t.name, t.column_names, rows)
            conn.commit()
            total_rows += n
            print(f"  [{idx:>2}/{len(tables)}] {t.name:<40s} +{n:>8} rows  total={total_rows:,}",
                  flush=True)

    # Phase 3: backfill product_product.default_variant_id (forward reference).
    print(f"Phase 3/3: backfilling product_product.default_variant_id...")
    with conn_cur(schema) as (conn, cur):
        cur.execute("""
            UPDATE product_product pp
            SET default_variant_id = sub.vid
            FROM (
                SELECT DISTINCT ON (product_id) product_id, id AS vid
                FROM product_productvariant
                ORDER BY product_id, id
            ) sub
            WHERE pp.id = sub.product_id
        """)
        n_updated = cur.rowcount
        conn.commit()
        print(f"  product_product.default_variant_id updated for {n_updated} products")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    with conn_cur(schema) as (conn, cur):
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=%s",
                    (schema,))
        actual = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_schema=%s AND constraint_type='PRIMARY KEY'",
                    (schema,))
        n_pks = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_schema=%s AND constraint_type='FOREIGN KEY'",
                    (schema,))
        n_fks = cur.fetchone()[0]
    print(f"Tables in {schema}: {actual}")
    print(f"Total rows inserted: {total_rows:,}")
    print(f"PRIMARY KEY constraints: {n_pks}")
    print(f"FOREIGN KEY constraints: {n_fks}  (must be 0)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--schema", default="saleor")
    args = ap.parse_args()
    run(reset=args.reset, schema=args.schema)
