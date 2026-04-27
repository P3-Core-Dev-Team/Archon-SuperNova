"""
Seed a Pagila-style (DVD-rental) dataset into Postgres schema ``dvdrental``.

Pagila is the canonical Postgres sample database (port of MySQL Sakila).
We replicate its 15-table shape with declared PKs and undeclared FKs so the
discovery pipeline must rediscover the relationships.

Schema overview:
    country -> city -> address ; address used by customer/staff/store
    language -> film ; film_actor / film_category -> film+actor / film+category
    store has manager_staff_id -> staff (and store -> address)
    inventory -> film + store
    rental -> inventory + customer + staff
    payment -> customer + staff + rental

Total: 15 tables, 22 declared FKs (we don't declare them; the pipeline
rediscovers them).

Usage:
    python3 seed_postgres_dvdrental.py
    python3 seed_postgres_dvdrental.py --schema dvdrental --reset
"""
from __future__ import annotations

import argparse
import random
import string
import time
from datetime import datetime, timedelta, timezone

from seed_postgres_500 import (
    DSN, FIRST_NAMES, LAST_NAMES, CITIES, COUNTRIES, SEED,
    Table, conn_cur, copy_rows, gen_email, already_populated,
)

random.seed(SEED)

CTX: dict[str, list] = {}


# ---- generators ------------------------------------------------------------

def t_country(rng: random.Random):
    rows = [(i + 1, name, datetime.now(timezone.utc))
            for i, name in enumerate(COUNTRIES[:60])]
    CTX["country_ids"] = [r[0] for r in rows]
    return rows


def t_city(rng: random.Random):
    rows = []
    next_id = 1
    for cc in CTX["country_ids"]:
        n = rng.randint(8, 25)
        for _ in range(n):
            rows.append((next_id, rng.choice(CITIES), cc,
                         datetime.now(timezone.utc)))
            next_id += 1
    CTX["city_ids"] = [r[0] for r in rows]
    return rows


def t_address(rng: random.Random):
    rows = []
    cities = CTX["city_ids"]
    for i in range(1, 5001):
        rows.append((
            i,
            f"{rng.randint(1, 9999)} {rng.choice(LAST_NAMES).title()} St",
            None,
            f"District{rng.randint(1, 50)}",
            rng.choice(cities),
            f"{rng.randint(10000, 99999)}",
            f"+1{rng.randint(2000000000, 9999999999)}",
            datetime.now(timezone.utc),
        ))
    CTX["address_ids"] = [r[0] for r in rows]
    return rows


def t_language(rng: random.Random):
    langs = ["English", "Italian", "Japanese", "Mandarin", "French",
            "German", "Spanish", "Portuguese", "Russian", "Korean"]
    rows = [(i + 1, n, datetime.now(timezone.utc))
            for i, n in enumerate(langs)]
    CTX["language_ids"] = [r[0] for r in rows]
    return rows


def t_category(rng: random.Random):
    cats = ["Action", "Animation", "Children", "Classics", "Comedy",
            "Documentary", "Drama", "Family", "Foreign", "Games",
            "Horror", "Music", "New", "Sci-Fi", "Sports", "Travel"]
    rows = [(i + 1, n, datetime.now(timezone.utc))
            for i, n in enumerate(cats)]
    CTX["category_ids"] = [r[0] for r in rows]
    return rows


def t_actor(rng: random.Random):
    rows = []
    for i in range(1, 401):
        rows.append((
            i,
            rng.choice(FIRST_NAMES).title(),
            rng.choice(LAST_NAMES).title(),
            datetime.now(timezone.utc),
        ))
    CTX["actor_ids"] = [r[0] for r in rows]
    return rows


def t_film(rng: random.Random):
    langs = CTX["language_ids"]
    rows = []
    titles = []
    for i in range(1, 2001):
        title = f"FILM {rng.choice(LAST_NAMES).upper()} {rng.choice(FIRST_NAMES).upper()}"
        titles.append(title)
        rows.append((
            i,
            title,
            f"A {rng.choice(['epic', 'thrilling', 'amazing'])} story.",
            rng.randint(1980, 2024),
            rng.choice(langs),
            # original_language_id: 30% set, 70% NULL — Pagila convention
            rng.choice(langs) if rng.random() < 0.3 else None,
            rng.choice([3, 5, 7]),
            round(rng.uniform(0.99, 4.99), 2),
            rng.randint(60, 180),
            round(rng.uniform(9.99, 29.99), 2),
            rng.choice(["G", "PG", "PG-13", "R", "NC-17"]),
            datetime.now(timezone.utc),
        ))
    CTX["film_ids"] = [r[0] for r in rows]
    return rows


def t_film_actor(rng: random.Random):
    rows = []
    seen = set()
    for fid in CTX["film_ids"]:
        n = rng.randint(3, 8)
        actors = rng.sample(CTX["actor_ids"], n)
        for aid in actors:
            if (aid, fid) in seen:
                continue
            seen.add((aid, fid))
            rows.append((aid, fid, datetime.now(timezone.utc)))
    return rows


def t_film_category(rng: random.Random):
    rows = []
    cats = CTX["category_ids"]
    for fid in CTX["film_ids"]:
        cid = rng.choice(cats)
        rows.append((fid, cid, datetime.now(timezone.utc)))
    return rows


def t_staff(rng: random.Random):
    addrs = CTX["address_ids"]
    rows = []
    # Staff first — store will reference manager_staff_id later. We seed staff
    # with store_id = 1 or 2 (Pagila has 2 stores). Pre-allocate store IDs.
    CTX["store_ids"] = [1, 2]
    for i in range(1, 21):
        first = rng.choice(FIRST_NAMES).title()
        last = rng.choice(LAST_NAMES).title()
        rows.append((
            i, first, last,
            rng.choice(addrs),
            gen_email(rng, first.lower(), last.lower()),
            rng.choice(CTX["store_ids"]),
            True,
            f"{first.lower()}.{last.lower()}",
            "x" * 40,
            datetime.now(timezone.utc),
        ))
    CTX["staff_ids"] = [r[0] for r in rows]
    return rows


def t_store(rng: random.Random):
    # Pick 2 staff to be managers
    managers = rng.sample(CTX["staff_ids"], 2)
    addrs = rng.sample(CTX["address_ids"], 2)
    rows = []
    for i, store_id in enumerate(CTX["store_ids"]):
        rows.append((
            store_id,
            managers[i],
            addrs[i],
            datetime.now(timezone.utc),
        ))
    return rows


def t_customer(rng: random.Random):
    addrs = CTX["address_ids"]
    stores = CTX["store_ids"]
    rows = []
    for i in range(1, 1501):
        first = rng.choice(FIRST_NAMES).title()
        last = rng.choice(LAST_NAMES).title()
        rows.append((
            i,
            rng.choice(stores),
            first, last,
            gen_email(rng, first.lower(), last.lower()),
            rng.choice(addrs),
            True,
            datetime.now(timezone.utc) - timedelta(days=rng.randint(30, 1500)),
            datetime.now(timezone.utc),
        ))
    CTX["customer_ids"] = [r[0] for r in rows]
    return rows


def t_inventory(rng: random.Random):
    rows = []
    next_id = 1
    stores = CTX["store_ids"]
    for fid in CTX["film_ids"]:
        copies = rng.randint(2, 4)
        for _ in range(copies):
            rows.append((
                next_id, fid, rng.choice(stores), datetime.now(timezone.utc)
            ))
            next_id += 1
    CTX["inventory_ids"] = [r[0] for r in rows]
    return rows


def t_rental(rng: random.Random):
    rows = []
    inv = CTX["inventory_ids"]
    cust = CTX["customer_ids"]
    staff = CTX["staff_ids"]
    rental_ids = []
    for i in range(1, 16001):
        rd = datetime.now(timezone.utc) - timedelta(days=rng.randint(1, 730))
        ret = rd + timedelta(days=rng.randint(1, 7)) if rng.random() < 0.95 else None
        rows.append((
            i, rd, rng.choice(inv), rng.choice(cust), ret,
            rng.choice(staff), datetime.now(timezone.utc),
        ))
        rental_ids.append(i)
    CTX["rental_ids"] = rental_ids
    return rows


def t_payment(rng: random.Random):
    rows = []
    rentals = CTX["rental_ids"]
    cust = CTX["customer_ids"]
    staff = CTX["staff_ids"]
    for i in range(1, 14001):
        # Each payment ties to a rental + the rental's customer + a staff
        rid = rng.choice(rentals)
        rows.append((
            i, rng.choice(cust), rng.choice(staff), rid,
            round(rng.uniform(0.99, 9.99), 2),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(1, 700)),
        ))
    return rows


# ---- table list ------------------------------------------------------------

def build_tables() -> list[Table]:
    return [
        Table("country",
              '''CREATE TABLE IF NOT EXISTS "country" (
                  country_id SERIAL PRIMARY KEY,
                  country TEXT NOT NULL,
                  last_update TIMESTAMPTZ)''',
              t_country,
              ["country_id", "country", "last_update"]),
        Table("city",
              '''CREATE TABLE IF NOT EXISTS "city" (
                  city_id SERIAL PRIMARY KEY,
                  city TEXT NOT NULL,
                  country_id INTEGER NOT NULL,
                  last_update TIMESTAMPTZ)''',
              t_city,
              ["city_id", "city", "country_id", "last_update"]),
        Table("address",
              '''CREATE TABLE IF NOT EXISTS "address" (
                  address_id SERIAL PRIMARY KEY,
                  address TEXT NOT NULL,
                  address2 TEXT,
                  district TEXT,
                  city_id INTEGER NOT NULL,
                  postal_code VARCHAR(10),
                  phone VARCHAR(20),
                  last_update TIMESTAMPTZ)''',
              t_address,
              ["address_id", "address", "address2", "district", "city_id",
               "postal_code", "phone", "last_update"]),
        Table("language",
              '''CREATE TABLE IF NOT EXISTS "language" (
                  language_id SERIAL PRIMARY KEY,
                  name TEXT NOT NULL,
                  last_update TIMESTAMPTZ)''',
              t_language,
              ["language_id", "name", "last_update"]),
        Table("category",
              '''CREATE TABLE IF NOT EXISTS "category" (
                  category_id SERIAL PRIMARY KEY,
                  name TEXT NOT NULL,
                  last_update TIMESTAMPTZ)''',
              t_category,
              ["category_id", "name", "last_update"]),
        Table("actor",
              '''CREATE TABLE IF NOT EXISTS "actor" (
                  actor_id SERIAL PRIMARY KEY,
                  first_name TEXT NOT NULL,
                  last_name TEXT NOT NULL,
                  last_update TIMESTAMPTZ)''',
              t_actor,
              ["actor_id", "first_name", "last_name", "last_update"]),
        Table("film",
              '''CREATE TABLE IF NOT EXISTS "film" (
                  film_id SERIAL PRIMARY KEY,
                  title TEXT NOT NULL,
                  description TEXT,
                  release_year INTEGER,
                  language_id INTEGER NOT NULL,
                  original_language_id INTEGER,
                  rental_duration INTEGER,
                  rental_rate NUMERIC(4,2),
                  length INTEGER,
                  replacement_cost NUMERIC(5,2),
                  rating TEXT,
                  last_update TIMESTAMPTZ)''',
              t_film,
              ["film_id", "title", "description", "release_year", "language_id",
               "original_language_id", "rental_duration", "rental_rate",
               "length", "replacement_cost", "rating", "last_update"]),
        Table("film_actor",
              '''CREATE TABLE IF NOT EXISTS "film_actor" (
                  actor_id INTEGER NOT NULL,
                  film_id INTEGER NOT NULL,
                  last_update TIMESTAMPTZ,
                  PRIMARY KEY (actor_id, film_id))''',
              t_film_actor,
              ["actor_id", "film_id", "last_update"]),
        Table("film_category",
              '''CREATE TABLE IF NOT EXISTS "film_category" (
                  film_id INTEGER NOT NULL,
                  category_id INTEGER NOT NULL,
                  last_update TIMESTAMPTZ,
                  PRIMARY KEY (film_id, category_id))''',
              t_film_category,
              ["film_id", "category_id", "last_update"]),
        Table("staff",
              '''CREATE TABLE IF NOT EXISTS "staff" (
                  staff_id SERIAL PRIMARY KEY,
                  first_name TEXT NOT NULL,
                  last_name TEXT NOT NULL,
                  address_id INTEGER NOT NULL,
                  email TEXT,
                  store_id INTEGER NOT NULL,
                  active BOOLEAN,
                  username TEXT,
                  password TEXT,
                  last_update TIMESTAMPTZ)''',
              t_staff,
              ["staff_id", "first_name", "last_name", "address_id", "email",
               "store_id", "active", "username", "password", "last_update"]),
        Table("store",
              '''CREATE TABLE IF NOT EXISTS "store" (
                  store_id SERIAL PRIMARY KEY,
                  manager_staff_id INTEGER NOT NULL,
                  address_id INTEGER NOT NULL,
                  last_update TIMESTAMPTZ)''',
              t_store,
              ["store_id", "manager_staff_id", "address_id", "last_update"]),
        Table("customer",
              '''CREATE TABLE IF NOT EXISTS "customer" (
                  customer_id SERIAL PRIMARY KEY,
                  store_id INTEGER NOT NULL,
                  first_name TEXT NOT NULL,
                  last_name TEXT NOT NULL,
                  email TEXT,
                  address_id INTEGER NOT NULL,
                  activebool BOOLEAN,
                  create_date TIMESTAMPTZ,
                  last_update TIMESTAMPTZ)''',
              t_customer,
              ["customer_id", "store_id", "first_name", "last_name", "email",
               "address_id", "activebool", "create_date", "last_update"]),
        Table("inventory",
              '''CREATE TABLE IF NOT EXISTS "inventory" (
                  inventory_id SERIAL PRIMARY KEY,
                  film_id INTEGER NOT NULL,
                  store_id INTEGER NOT NULL,
                  last_update TIMESTAMPTZ)''',
              t_inventory,
              ["inventory_id", "film_id", "store_id", "last_update"]),
        Table("rental",
              '''CREATE TABLE IF NOT EXISTS "rental" (
                  rental_id SERIAL PRIMARY KEY,
                  rental_date TIMESTAMPTZ NOT NULL,
                  inventory_id INTEGER NOT NULL,
                  customer_id INTEGER NOT NULL,
                  return_date TIMESTAMPTZ,
                  staff_id INTEGER NOT NULL,
                  last_update TIMESTAMPTZ)''',
              t_rental,
              ["rental_id", "rental_date", "inventory_id", "customer_id",
               "return_date", "staff_id", "last_update"]),
        Table("payment",
              '''CREATE TABLE IF NOT EXISTS "payment" (
                  payment_id SERIAL PRIMARY KEY,
                  customer_id INTEGER NOT NULL,
                  staff_id INTEGER NOT NULL,
                  rental_id INTEGER NOT NULL,
                  amount NUMERIC(5,2) NOT NULL,
                  payment_date TIMESTAMPTZ NOT NULL)''',
              t_payment,
              ["payment_id", "customer_id", "staff_id", "rental_id",
               "amount", "payment_date"]),
    ]


def run(reset: bool, schema: str):
    import psycopg2
    tables = build_tables()
    t0 = time.time()

    print(f"Phase 1/2: schema {schema!r} DDL...")
    conn = psycopg2.connect(**DSN); conn.autocommit = True
    cur = conn.cursor()
    if reset:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    cur.execute(f'SET search_path TO "{schema}"')
    for t in tables:
        cur.execute(t.ddl)
    conn.close()
    print(f"  DDL done in {time.time()-t0:.1f}s")

    print(f"Phase 2/2: populating tables in schema {schema!r}...")
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
            print(f"  [{idx:>2}/{len(tables)}] {t.name:<20s} +{n:>8} rows  total={total_rows:,}",
                  flush=True)

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
    ap.add_argument("--schema", default="dvdrental")
    args = ap.parse_args()
    run(reset=args.reset, schema=args.schema)
