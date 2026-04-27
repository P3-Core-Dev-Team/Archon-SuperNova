"""
Seed ~500 tables into a Postgres `public` schema with NO FK constraints declared.

Mix:
  - 30 thematic tables with realistic enterprise schemas + UNDECLARED FK
    relationships the discovery pipeline should find (customers, orders,
    order_items, payments, ...).
  - ~470 generated tables across exclusion-pattern families (logs, bak,
    archive, events, etl, temp, tmp, migrations, snapshot), reference/lookups,
    KPI/reporting, junction (M:N), wide-denormalized, and a couple of empties.

PII columns (emails, phones, SSNs, IBANs, Luhn-valid cards, API keys, DOBs)
are scattered through customers, employees, payments, api_tokens.

Idempotent: CREATE TABLE IF NOT EXISTS; skips data insert when table already
has rows. Re-running produces the same state.

Usage:
    python3 seed_postgres_500.py
    python3 seed_postgres_500.py --reset   # DROP + recreate everything

Connection: localhost:5432 db=test schema=public user=adsuser pass=Ads@3421
"""
from __future__ import annotations

import argparse
import io
import random
import secrets
import string
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Iterator

import psycopg2
from psycopg2.extras import execute_batch

DSN = dict(host="localhost", port=5432, dbname="test", user="adsuser",
           password="Ads@3421", connect_timeout=10)

SEED = 42
random.seed(SEED)


# --------------------------------------------------------------------- helpers

def luhn_checksum_digit(prefix: str) -> str:
    digits = [int(c) for c in prefix]
    s = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return str((10 - s % 10) % 10)


def luhn_card(rng: random.Random) -> str:
    prefix_choices = [("4", 16), ("51", 16), ("52", 16), ("53", 16), ("34", 15), ("37", 15)]
    prefix, total_len = rng.choice(prefix_choices)
    body = prefix + "".join(rng.choices(string.digits, k=total_len - len(prefix) - 1))
    return body + luhn_checksum_digit(body)


def gen_iban(rng: random.Random) -> str:
    country = rng.choice(["GB", "DE", "FR", "ES", "NL", "IE"])
    bban = "".join(rng.choices(string.digits + string.ascii_uppercase, k=18))
    rearranged = bban + country + "00"
    digits = "".join(str(int(c, 36)) for c in rearranged)
    check = 98 - int(digits) % 97
    return f"{country}{check:02d}{bban}"


def gen_ssn(rng: random.Random) -> str:
    while True:
        a = rng.randint(1, 899)
        if a in (666,) or 900 <= a <= 999:
            continue
        b = rng.randint(1, 99)
        c = rng.randint(1, 9999)
        return f"{a:03d}-{b:02d}-{c:04d}"


def gen_email(rng: random.Random, first: str, last: str) -> str:
    domain = rng.choice(["example.com", "acme.io", "globex.net", "umbrella.org",
                         "initech.com", "hooli.co", "starkindustries.com",
                         "wayneenterprises.io", "stark-tech.de", "soylent.co.uk"])
    sep = rng.choice([".", "_", ""])
    return f"{first.lower()}{sep}{last.lower()}{rng.randint(1, 999)}@{domain}"


def gen_phone_e164(rng: random.Random) -> str:
    cc = rng.choice(["+1", "+44", "+49", "+33", "+34", "+91", "+81"])
    body = "".join(rng.choices(string.digits, k=10))
    return f"{cc}{body}"


def gen_uuid(rng: random.Random) -> str:
    h = "".join(rng.choices("0123456789abcdef", k=32))
    return f"{h[0:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:32]}"


FIRST_NAMES = ["alex", "jamie", "morgan", "casey", "taylor", "jordan", "riley",
               "drew", "skyler", "robin", "harper", "logan", "emery", "rowan",
               "sage", "ari", "leo", "noor", "raj", "priya", "chen", "lin",
               "mateo", "sofia", "ana", "ivan", "olga", "akiko", "yuki",
               "kenji", "hiro", "lukas", "nora", "elena", "diego", "valentina"]
LAST_NAMES = ["smith", "jones", "brown", "garcia", "rossi", "schmidt", "martin",
              "kumar", "patel", "khan", "wang", "li", "chen", "petrov", "ivanov",
              "kowalski", "novak", "andersson", "okafor", "abebe", "santos",
              "rivera", "fernandez", "delaney", "fitzgerald", "hayes", "wright",
              "bennett", "harris", "carter", "phillips", "morgan", "long", "ross"]
CITIES = ["New York", "London", "Berlin", "Tokyo", "Mumbai", "Paris", "Madrid",
          "Sao Paulo", "Sydney", "Toronto", "Dubai", "Singapore", "Lagos",
          "Mexico City", "Cairo", "Istanbul", "Buenos Aires", "Seoul", "Jakarta"]
COUNTRIES = ["US", "GB", "DE", "JP", "IN", "FR", "ES", "BR", "AU", "CA", "AE",
             "SG", "NG", "MX", "EG", "TR", "AR", "KR", "ID"]
CURRENCIES = ["USD", "GBP", "EUR", "JPY", "INR", "CAD", "AUD", "BRL", "MXN", "AED"]


# --------------------------------------------------------------- DDL utilities

@dataclass
class Table:
    name: str
    ddl: str
    populate: Callable[[random.Random], list[tuple] | None] | None = None
    column_names: list[str] = field(default_factory=list)
    excluded_reason: str | None = None  # purely informational


def copy_rows(cur, table: str, columns: list[str], rows: Iterable[tuple]) -> int:
    """Bulk insert via COPY FROM STDIN. Rows must be tuples of stringifiable values."""
    buf = io.StringIO()
    n = 0
    for row in rows:
        out = []
        for v in row:
            if v is None:
                out.append("\\N")
            else:
                s = str(v)
                # escape special chars for COPY text format
                s = s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
                out.append(s)
            n += 0  # silence
        buf.write("\t".join(out) + "\n")
        n += 1
    if n == 0:
        return 0
    buf.seek(0)
    cols = ",".join(f'"{c}"' for c in columns)
    cur.copy_expert(f'COPY "{table}" ({cols}) FROM STDIN', buf)
    return n


# ---------------------------------------------------- thematic core (with FKs)

# Returns a list of Table objects with realistic schemas + populate funcs that
# emit rows where child tables draw FK values from the actual parent id space.
# We carry the generated id arrays in a shared dict between populate calls.

CONTEXT: dict[str, list] = {}


def t_categories(rng: random.Random):
    rows = []
    for i in range(1, 51):
        parent = None if i <= 8 else rng.randint(1, 8)
        rows.append((i, f"cat_{i:03d}", parent, f"Description {i}"))
    CONTEXT["category_ids"] = [r[0] for r in rows]
    return rows


def t_products(rng: random.Random):
    rows = []
    cats = CONTEXT["category_ids"]
    for i in range(1, 5001):
        rows.append((
            i,
            rng.choice(cats),
            f"sku-{i:06d}",
            f"Product {i}",
            round(rng.uniform(5.0, 5000.0), 2),
            rng.choice([True, False]),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 1500)),
        ))
    CONTEXT["product_ids"] = [r[0] for r in rows]
    CONTEXT["product_prices"] = {r[0]: r[4] for r in rows}
    return rows


def t_customers(rng: random.Random):
    rows = []
    for i in range(1, 50001):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        rows.append((
            i,
            gen_uuid(rng),
            first.title(),
            last.title(),
            gen_email(rng, first, last),
            gen_phone_e164(rng) if rng.random() > 0.15 else None,
            (date.today() - timedelta(days=rng.randint(18*365, 85*365))),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 1825)),
        ))
    CONTEXT["customer_ids"] = [r[0] for r in rows]
    return rows


def t_addresses(rng: random.Random):
    rows = []
    cust_ids = CONTEXT["customer_ids"]
    for i in range(1, 80001):
        cid = rng.choice(cust_ids) if rng.random() > 0.02 else None
        rows.append((
            i, cid,
            f"{rng.randint(1, 9999)} {rng.choice(LAST_NAMES).title()} St",
            rng.choice(CITIES),
            rng.choice(COUNTRIES),
            f"{rng.randint(10000, 99999)}",
        ))
    CONTEXT["address_ids"] = [r[0] for r in rows]
    return rows


def t_orders(rng: random.Random):
    cust_ids = CONTEXT["customer_ids"]
    addr_ids = CONTEXT["address_ids"]
    # zipfian-ish customer skew
    weighted_pool = []
    cnt = len(cust_ids)
    top5 = max(1, cnt // 20)
    weighted_pool.extend(cust_ids[:top5] * 10)
    weighted_pool.extend(cust_ids[top5:])
    rows = []
    for i in range(1, 100001):  # 100K orders
        rows.append((
            i,
            rng.choice(weighted_pool),
            rng.choice(addr_ids),
            rng.choice(["pending", "paid", "shipped", "delivered", "cancelled"]),
            rng.choice(CURRENCIES),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 1095)),
        ))
    CONTEXT["order_ids"] = [r[0] for r in rows]
    return rows


def t_order_items(rng: random.Random):
    order_ids = CONTEXT["order_ids"]
    product_ids = CONTEXT["product_ids"]
    prices = CONTEXT["product_prices"]
    rows = []
    next_id = 1
    subtotals: dict[int, float] = {}
    for oid in order_ids:
        n_items = max(1, int(rng.lognormvariate(0.7, 0.6)))
        sub = 0.0
        for _ in range(n_items):
            pid = rng.choice(product_ids)
            qty = max(1, int(rng.lognormvariate(0.3, 0.5)))
            unit = prices[pid]
            sub += unit * qty
            rows.append((next_id, oid, pid, qty, unit))
            next_id += 1
        subtotals[oid] = round(sub, 2)
    CONTEXT["order_subtotals"] = subtotals
    return rows


def t_payments(rng: random.Random):
    order_ids = CONTEXT["order_ids"]
    cust_ids = CONTEXT["customer_ids"]
    subs = CONTEXT["order_subtotals"]
    rows = []
    for i, oid in enumerate(order_ids, start=1):
        card = luhn_card(rng)
        iban = gen_iban(rng) if rng.random() > 0.4 else None
        rows.append((
            i,
            oid,
            rng.choice(cust_ids),
            gen_uuid(rng),
            card[-4:],
            card,
            iban,
            subs[oid],
            rng.choice(CURRENCIES),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 1095)),
        ))
    return rows


def t_warehouses(rng: random.Random):
    return [(i, f"WH-{i:03d}", rng.choice(CITIES), rng.choice(COUNTRIES))
            for i in range(1, 21)]


def t_inventory(rng: random.Random):
    pids = CONTEXT["product_ids"]
    return [(i, pids[i-1], rng.randint(0, 5000)) for i in range(1, 5001)]


def t_warehouse_stock(rng: random.Random):
    pids = CONTEXT["product_ids"]
    rows = []
    for i in range(1, 100001):
        rows.append((i, rng.choice(pids), rng.randint(1, 20),
                     rng.randint(0, 1500),
                     # tracking numbers — CC-shaped but fail Luhn
                     "".join(rng.choices(string.digits, k=16))))
    return rows


def t_users(rng: random.Random):
    cust_ids = CONTEXT["customer_ids"]
    rows = []
    for i in range(1, 10001):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        cid = rng.choice(cust_ids) if rng.random() > 0.30 else None
        rows.append((
            i, cid, f"{first.lower()}.{last.lower()}{i}",
            gen_email(rng, first, last),
            secrets.token_hex(32),  # password_hash
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 1500)),
        ))
    CONTEXT["user_ids"] = [r[0] for r in rows]
    return rows


def t_roles(rng: random.Random):
    role_names = ["admin", "manager", "engineer", "support", "sales", "finance",
                  "hr", "ops", "auditor", "guest", "readonly", "developer",
                  "marketing", "executive", "intern", "contractor", "vendor",
                  "partner", "customer", "viewer"]
    return [(i, name, f"{name} role description")
            for i, name in enumerate(role_names, start=1)]


def t_user_roles(rng: random.Random):
    uids = CONTEXT["user_ids"]
    rows = []
    for i in range(1, 15001):
        rows.append((i, rng.choice(uids), rng.randint(1, 20)))
    return rows


def t_user_sessions(rng: random.Random):
    uids = CONTEXT["user_ids"]
    rows = []
    for i in range(1, 50001):
        ts = datetime.now(timezone.utc) - timedelta(seconds=rng.randint(0, 30*86400))
        rows.append((i, gen_uuid(rng), rng.choice(uids), ts,
                     f"{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(0,255)}"))
    return rows


def t_api_tokens(rng: random.Random):
    uids = CONTEXT["user_ids"]
    return [(i, rng.choice(uids), secrets.token_urlsafe(40),
             secrets.token_hex(32),
             datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)))
            for i in range(1, 2001)]


def t_employees(rng: random.Random):
    rows = []
    manager_pool = list(range(1, 51))  # first 50 are managers
    for i in range(1, 1501):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        manager = None if i <= 50 else rng.choice(manager_pool)
        rows.append((
            i,
            f"{first.title()} {last.title()}",
            f"{rng.choice(['EMP', 'STF'])}-{i:06d}",
            gen_ssn(rng),
            (date.today() - timedelta(days=rng.randint(22*365, 65*365))),
            gen_email(rng, first, last),
            gen_email(rng, first, last) if rng.random() > 0.4 else None,
            gen_phone_e164(rng),
            manager,
            datetime.now(timezone.utc) - timedelta(days=rng.randint(30, 8000)),
        ))
    CONTEXT["employee_ids"] = [r[0] for r in rows]
    return rows


def t_departments(rng: random.Random):
    emp_ids = CONTEXT["employee_ids"][:200]
    names = ["Engineering", "Sales", "Marketing", "Finance", "HR", "Operations",
             "Legal", "Customer Support", "Product", "Design", "Data", "Security",
             "Infrastructure", "Research", "Compliance", "Procurement",
             "Training", "Analytics", "Communications", "Investor Relations",
             "Strategy", "Logistics", "QA", "DevOps", "BI", "Risk", "Audit",
             "Treasury", "Tax", "Real Estate"]
    return [(i, n, rng.choice(emp_ids)) for i, n in enumerate(names, start=1)]


def t_tickets(rng: random.Random):
    cust_ids = CONTEXT["customer_ids"]
    emp_ids = CONTEXT["employee_ids"]
    rows = []
    for i in range(1, 20001):
        rows.append((
            i, rng.choice(cust_ids), rng.choice(emp_ids),
            rng.choice(["open", "pending", "resolved", "closed"]),
            f"Ticket #{i}: " + rng.choice(["billing issue", "delivery delay",
                                           "product question", "refund request"]),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 1095)),
        ))
    CONTEXT["ticket_ids"] = [r[0] for r in rows]
    return rows


def t_ticket_messages(rng: random.Random):
    tids = CONTEXT["ticket_ids"]
    uids = CONTEXT["user_ids"]
    rows = []
    for i in range(1, 60001):
        # ~5% messages embed PII in prose
        pii = ""
        if rng.random() < 0.05:
            email = gen_email(rng, rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))
            phone = gen_phone_e164(rng)
            pii = f" Please reach me at {email} or {phone}."
        body = f"Message {i} regarding ticket.{pii}"
        rows.append((i, rng.choice(tids), rng.choice(uids), body,
                     datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 1000))))
    return rows


def t_reviews(rng: random.Random):
    pids = CONTEXT["product_ids"]
    cids = CONTEXT["customer_ids"]
    rows = []
    for i in range(1, 15001):
        rows.append((i, rng.choice(pids), rng.choice(cids),
                     rng.randint(1, 5), f"Review {i}: opinions and details.",
                     datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 730))))
    return rows


THEMATIC_TABLES: list[Table] = [
    Table(
        "categories",
        """CREATE TABLE IF NOT EXISTS categories (
            id INTEGER, name TEXT, parent_category_id INTEGER, description TEXT
        )""",
        t_categories,
        column_names=["id", "name", "parent_category_id", "description"],
    ),
    Table(
        "products",
        """CREATE TABLE IF NOT EXISTS products (
            id BIGINT, category_id INTEGER, sku VARCHAR(64), name TEXT,
            price NUMERIC(10,2), is_active BOOLEAN, created_at TIMESTAMPTZ
        )""",
        t_products,
        column_names=["id", "category_id", "sku", "name", "price", "is_active", "created_at"],
    ),
    Table(
        "customers",
        """CREATE TABLE IF NOT EXISTS customers (
            id BIGINT, external_id UUID, first_name TEXT, last_name TEXT,
            email VARCHAR(128), phone VARCHAR(32), dob DATE, created_at TIMESTAMPTZ
        )""",
        t_customers,
        column_names=["id", "external_id", "first_name", "last_name", "email", "phone", "dob", "created_at"],
    ),
    Table(
        "addresses",
        """CREATE TABLE IF NOT EXISTS addresses (
            id BIGINT, customer_id BIGINT, street TEXT, city TEXT,
            country VARCHAR(8), postal_code VARCHAR(16)
        )""",
        t_addresses,
        column_names=["id", "customer_id", "street", "city", "country", "postal_code"],
    ),
    Table(
        "orders",
        """CREATE TABLE IF NOT EXISTS orders (
            id BIGINT, customer_id BIGINT, shipping_address_id BIGINT,
            status TEXT, currency VARCHAR(8), created_at TIMESTAMPTZ
        )""",
        t_orders,
        column_names=["id", "customer_id", "shipping_address_id", "status", "currency", "created_at"],
    ),
    Table(
        "order_items",
        """CREATE TABLE IF NOT EXISTS order_items (
            id BIGINT, order_id BIGINT, product_id BIGINT,
            quantity INTEGER, unit_price NUMERIC(10,2)
        )""",
        t_order_items,
        column_names=["id", "order_id", "product_id", "quantity", "unit_price"],
    ),
    Table(
        "payments",
        """CREATE TABLE IF NOT EXISTS payments (
            id BIGINT, order_id BIGINT, customer_id BIGINT,
            transaction_id UUID, card_number_last4 VARCHAR(4),
            card_number_raw VARCHAR(19), iban VARCHAR(34),
            amount NUMERIC(12,2), currency VARCHAR(8), created_at TIMESTAMPTZ
        )""",
        t_payments,
        column_names=["id", "order_id", "customer_id", "transaction_id", "card_number_last4",
                      "card_number_raw", "iban", "amount", "currency", "created_at"],
    ),
    Table(
        "warehouses",
        """CREATE TABLE IF NOT EXISTS warehouses (
            id INTEGER, code VARCHAR(16), city TEXT, country VARCHAR(8)
        )""",
        t_warehouses,
        column_names=["id", "code", "city", "country"],
    ),
    Table(
        "inventory",
        """CREATE TABLE IF NOT EXISTS inventory (
            id BIGINT, product_id BIGINT, qty_on_hand INTEGER
        )""",
        t_inventory,
        column_names=["id", "product_id", "qty_on_hand"],
    ),
    Table(
        "warehouse_stock",
        """CREATE TABLE IF NOT EXISTS warehouse_stock (
            id BIGINT, product_id BIGINT, warehouse_id INTEGER,
            qty INTEGER, tracking_number VARCHAR(20)
        )""",
        t_warehouse_stock,
        column_names=["id", "product_id", "warehouse_id", "qty", "tracking_number"],
    ),
    Table(
        "users",
        """CREATE TABLE IF NOT EXISTS users (
            id BIGINT, customer_id BIGINT, username VARCHAR(64),
            email VARCHAR(128), password_hash TEXT, created_at TIMESTAMPTZ
        )""",
        t_users,
        column_names=["id", "customer_id", "username", "email", "password_hash", "created_at"],
    ),
    Table(
        "roles",
        """CREATE TABLE IF NOT EXISTS roles (
            id INTEGER, name VARCHAR(32), description TEXT
        )""",
        t_roles,
        column_names=["id", "name", "description"],
    ),
    Table(
        "user_roles",
        """CREATE TABLE IF NOT EXISTS user_roles (
            id BIGINT, user_id BIGINT, role_id INTEGER
        )""",
        t_user_roles,
        column_names=["id", "user_id", "role_id"],
    ),
    Table(
        "user_sessions",
        """CREATE TABLE IF NOT EXISTS user_sessions (
            id BIGINT, session_id UUID, user_id BIGINT,
            started_at TIMESTAMPTZ, ip_address VARCHAR(45)
        )""",
        t_user_sessions,
        column_names=["id", "session_id", "user_id", "started_at", "ip_address"],
    ),
    Table(
        "api_tokens",
        """CREATE TABLE IF NOT EXISTS api_tokens (
            id BIGINT, user_id BIGINT, token TEXT,
            secret_hash TEXT, created_at TIMESTAMPTZ
        )""",
        t_api_tokens,
        column_names=["id", "user_id", "token", "secret_hash", "created_at"],
    ),
    Table(
        "employee_records",
        """CREATE TABLE IF NOT EXISTS employee_records (
            id INTEGER, full_name TEXT, employee_id VARCHAR(20),
            ssn VARCHAR(11), dob DATE, work_email VARCHAR(128),
            personal_email VARCHAR(128), phone VARCHAR(32),
            manager_id INTEGER, hired_at TIMESTAMPTZ
        )""",
        t_employees,
        column_names=["id", "full_name", "employee_id", "ssn", "dob", "work_email",
                      "personal_email", "phone", "manager_id", "hired_at"],
    ),
    Table(
        "departments",
        """CREATE TABLE IF NOT EXISTS departments (
            id INTEGER, name TEXT, head_employee_id INTEGER
        )""",
        t_departments,
        column_names=["id", "name", "head_employee_id"],
    ),
    Table(
        "tickets",
        """CREATE TABLE IF NOT EXISTS tickets (
            id BIGINT, customer_id BIGINT, assigned_to INTEGER,
            status TEXT, subject TEXT, created_at TIMESTAMPTZ
        )""",
        t_tickets,
        column_names=["id", "customer_id", "assigned_to", "status", "subject", "created_at"],
    ),
    Table(
        "ticket_messages",
        """CREATE TABLE IF NOT EXISTS ticket_messages (
            id BIGINT, ticket_id BIGINT, author_user_id BIGINT,
            body TEXT, created_at TIMESTAMPTZ
        )""",
        t_ticket_messages,
        column_names=["id", "ticket_id", "author_user_id", "body", "created_at"],
    ),
    Table(
        "reviews",
        """CREATE TABLE IF NOT EXISTS reviews (
            id BIGINT, product_id BIGINT, customer_id BIGINT,
            rating INTEGER, body TEXT, created_at TIMESTAMPTZ
        )""",
        t_reviews,
        column_names=["id", "product_id", "customer_id", "rating", "body", "created_at"],
    ),
]


# ----------------------------------------------------------- generated tables

def _gen_simple_dim(name: str, n_rows: int) -> Table:
    """A simple dimension-like table: id, code, name, created_at."""
    ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
        id INTEGER, code VARCHAR(32), name TEXT, created_at TIMESTAMPTZ
    )'''

    def populate(rng: random.Random):
        return [(i, f"{name[:6].upper()}-{i:05d}",
                 f"{name} entry {i}",
                 datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 3000)))
                for i in range(1, n_rows + 1)]

    return Table(name, ddl, populate, column_names=["id", "code", "name", "created_at"])


def _gen_log_table(name: str, n_rows: int, reason: str) -> Table:
    ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
        id BIGINT, event_ts TIMESTAMPTZ, level VARCHAR(8),
        message TEXT, source VARCHAR(64)
    )'''

    def populate(rng: random.Random):
        return [(i, datetime.now(timezone.utc) - timedelta(seconds=rng.randint(0, 30*86400)),
                 rng.choice(["INFO", "WARN", "ERROR", "DEBUG"]),
                 f"Log line {i}",
                 rng.choice(["api", "worker", "scheduler", "auth", "http"]))
                for i in range(1, n_rows + 1)]

    return Table(name, ddl, populate,
                 column_names=["id", "event_ts", "level", "message", "source"],
                 excluded_reason=reason)


def _gen_kpi_table(name: str, n_rows: int) -> Table:
    ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
        id INTEGER, period DATE, metric_name VARCHAR(64),
        value NUMERIC(18,4), dimension VARCHAR(64)
    )'''

    def populate(rng: random.Random):
        return [(i,
                 date.today() - timedelta(days=rng.randint(0, 730)),
                 rng.choice(["revenue", "users", "sessions", "conversions",
                             "churn_rate", "arpu", "ltv", "nps"]),
                 round(rng.uniform(0, 1_000_000), 4),
                 rng.choice(["global", "us", "eu", "apac", "rest"]))
                for i in range(1, n_rows + 1)]

    return Table(name, ddl, populate,
                 column_names=["id", "period", "metric_name", "value", "dimension"])


def _gen_junction_table(name: str, left_ids: list[int] | None, right_ids: list[int] | None,
                       n_rows: int) -> Table:
    ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
        id BIGINT, left_ref BIGINT, right_ref BIGINT, weight REAL
    )'''

    def populate(rng: random.Random):
        left = left_ids or list(range(1, 5001))
        right = right_ids or list(range(1, 5001))
        return [(i, rng.choice(left), rng.choice(right), rng.uniform(0, 1))
                for i in range(1, n_rows + 1)]

    return Table(name, ddl, populate,
                 column_names=["id", "left_ref", "right_ref", "weight"])


def _gen_wide_table(name: str, n_cols: int, n_rows: int) -> Table:
    cols = ["id BIGINT"] + [f"col_{i:03d} TEXT" for i in range(1, n_cols + 1)]
    ddl = f'CREATE TABLE IF NOT EXISTS "{name}" ({", ".join(cols)})'

    def populate(rng: random.Random):
        rows = []
        for i in range(1, n_rows + 1):
            row = [i] + [f"v{rng.randint(0, 999)}" for _ in range(n_cols)]
            rows.append(tuple(row))
        return rows

    return Table(name, ddl, populate,
                 column_names=["id"] + [f"col_{i:03d}" for i in range(1, n_cols + 1)])


def _gen_empty_table(name: str) -> Table:
    ddl = f'CREATE TABLE IF NOT EXISTS "{name}" (id BIGINT, payload TEXT)'
    return Table(name, ddl, populate=None, column_names=["id", "payload"])


def _gen_tmp_table(name: str, reason: str) -> Table:
    ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
        batch_id BIGINT, raw_payload TEXT, ingested_at TIMESTAMPTZ
    )'''

    def populate(rng: random.Random):
        return [(i, f"raw_data_blob_{i}",
                 datetime.now(timezone.utc) - timedelta(hours=rng.randint(0, 24*30)))
                for i in range(1, 1001)]

    return Table(name, ddl, populate,
                 column_names=["batch_id", "raw_payload", "ingested_at"],
                 excluded_reason=reason)


def _gen_archive_table(name: str, reason: str) -> Table:
    ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
        id BIGINT, snapshot_at TIMESTAMPTZ, payload TEXT
    )'''

    def populate(rng: random.Random):
        return [(i,
                 datetime.now(timezone.utc) - timedelta(days=rng.randint(365, 1500)),
                 f"archived_record_{i}")
                for i in range(1, 5001)]

    return Table(name, ddl, populate,
                 column_names=["id", "snapshot_at", "payload"],
                 excluded_reason=reason)


def _gen_event_table(name: str, reason: str) -> Table:
    ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
        id BIGINT, user_id BIGINT, event_name VARCHAR(64),
        properties TEXT, occurred_at TIMESTAMPTZ
    )'''

    def populate(rng: random.Random):
        uids = CONTEXT.get("user_ids", list(range(1, 10001)))
        return [(i, rng.choice(uids),
                 rng.choice(["click", "view", "submit", "login", "logout",
                             "purchase", "scroll", "search"]),
                 f'{{"x":{rng.randint(0,1000)}}}',
                 datetime.now(timezone.utc) - timedelta(seconds=rng.randint(0, 30*86400)))
                for i in range(1, 30001)]

    return Table(name, ddl, populate,
                 column_names=["id", "user_id", "event_name", "properties", "occurred_at"],
                 excluded_reason=reason)


def _gen_etl_table(name: str, reason: str) -> Table:
    ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
        job_id BIGINT, source TEXT, status VARCHAR(16),
        rows_in INTEGER, rows_out INTEGER, run_at TIMESTAMPTZ
    )'''

    def populate(rng: random.Random):
        return [(i,
                 rng.choice(["s3://bucket-a", "kafka://topic-x", "ftp://srv1"]),
                 rng.choice(["queued", "running", "succeeded", "failed"]),
                 rng.randint(0, 100000), rng.randint(0, 100000),
                 datetime.now(timezone.utc) - timedelta(hours=rng.randint(0, 24*60)))
                for i in range(1, 3001)]

    return Table(name, ddl, populate,
                 column_names=["job_id", "source", "status", "rows_in", "rows_out", "run_at"],
                 excluded_reason=reason)


# Build the full table list ---------------------------------------------------

def build_all_tables() -> list[Table]:
    tables: list[Table] = list(THEMATIC_TABLES)

    # 50 reference / lookup dimensions (small)
    refs = ["country_codes", "currency_codes", "language_codes", "timezone_codes",
            "industry_codes", "department_codes", "shipping_methods",
            "payment_methods", "subscription_tiers", "billing_intervals",
            "tax_categories", "vat_rates", "discount_types", "loyalty_tiers",
            "fraud_reasons", "return_reasons", "refund_reasons", "shipment_statuses",
            "order_statuses", "ticket_priorities", "ticket_categories",
            "complaint_categories", "feature_flags", "experiment_variants",
            "ab_tests", "campaign_types", "channel_codes", "media_types",
            "device_types", "platform_codes", "browser_codes", "os_codes",
            "locale_codes", "color_codes", "size_codes", "unit_codes",
            "package_types", "warehouse_zones", "carrier_codes",
            "supplier_categories", "vendor_tiers", "rfp_statuses",
            "po_statuses", "invoice_statuses", "credit_terms",
            "lead_sources", "campaign_statuses", "marketing_lists",
            "segment_codes", "audience_codes",
            "fiscal_periods", "tax_jurisdictions", "regulatory_zones",
            "warehouse_bins", "asset_classes", "depreciation_methods",
            "incoterms", "harmonized_codes", "iso_standards", "kpi_definitions",
            "alert_levels", "incident_severities", "compliance_tags",
            "data_classifications", "retention_policies"]
    for r in refs:
        tables.append(_gen_simple_dim(r, random.Random(SEED + hash(r)).randint(20, 200)))

    # 70 dim_* tables
    for i in range(1, 71):
        tables.append(_gen_simple_dim(f"dim_entity_{i:03d}",
                                       random.Random(SEED + i).randint(500, 5000)))

    # 70 fact_* tables (small fact stub schemas)
    for i in range(1, 71):
        name = f"fact_metric_{i:03d}"
        ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
            id BIGINT, dim_id INTEGER, ts TIMESTAMPTZ, value NUMERIC(18,4)
        )'''

        def make_pop(ii):
            def populate(rng: random.Random):
                return [(j, rng.randint(1, 1000),
                         datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
                         round(rng.uniform(0, 100000), 4))
                        for j in range(1, random.Random(SEED + 600 + ii).randint(500, 3000) + 1)]
            return populate
        tables.append(Table(name, ddl, make_pop(i),
                            column_names=["id", "dim_id", "ts", "value"]))

    # 70 kpi/reporting tables
    for i in range(1, 71):
        tables.append(_gen_kpi_table(f"kpi_metrics_{i:03d}",
                                      random.Random(SEED + 100 + i).randint(500, 3000)))

    # 80 log tables (excluded by *_log)
    for i in range(1, 81):
        tables.append(_gen_log_table(f"system_log_{i:03d}",
                                      random.Random(SEED + 200 + i).randint(5000, 30000),
                                      reason="log_pattern"))

    # 30 audit_log/access_log/error_log/api_log/security_log subtypes
    for prefix in ["audit", "access", "error", "api", "security", "request"]:
        for i in range(1, 6):
            tables.append(_gen_log_table(f"{prefix}_log_{i:02d}",
                                          random.Random(SEED + hash(prefix) + i).randint(2000, 15000),
                                          reason="log_pattern"))

    # 40 events tables (excluded by *_events)
    for i in range(1, 41):
        tables.append(_gen_event_table(f"user_events_{i:03d}", reason="events_pattern"))

    # 30 bak/archive tables (excluded)
    for i in range(1, 16):
        tables.append(_gen_archive_table(f"orders_bak_{i:03d}", reason="backup_pattern"))
    for i in range(1, 16):
        tables.append(_gen_archive_table(f"customers_archive_{i:03d}", reason="archive_pattern"))

    # 30 temp/tmp tables (excluded)
    for i in range(1, 16):
        tables.append(_gen_tmp_table(f"temp_import_{i:03d}", reason="temp_pattern"))
    for i in range(1, 16):
        tables.append(_gen_tmp_table(f"tmp_staging_{i:03d}", reason="tmp_pattern"))

    # 20 etl tables (excluded by etl_*)
    for i in range(1, 21):
        tables.append(_gen_etl_table(f"etl_pipeline_{i:03d}", reason="etl_pattern"))

    # 5 migrations
    for i in range(1, 6):
        tables.append(Table(
            f"migrations_v{i}",
            f'''CREATE TABLE IF NOT EXISTS "migrations_v{i}" (
                id INTEGER, name TEXT, applied_at TIMESTAMPTZ, checksum VARCHAR(64)
            )''',
            (lambda rng, ii=i: [(j, f"migration_{ii}_{j}",
                                  datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 1500)),
                                  secrets.token_hex(32))
                                 for j in range(1, 51)]),
            column_names=["id", "name", "applied_at", "checksum"],
            excluded_reason="migrations_pattern",
        ))

    # 30 junction (M:N) tables — using core ids so undeclared FKs are present
    junction_specs = [
        ("user_session_logs", "user_ids", None, 8000),
        ("customer_segments", "customer_ids", None, 12000),
        ("product_tags", "product_ids", None, 8000),
        ("product_promotions", "product_ids", None, 6000),
        ("order_tags", "order_ids", None, 10000),
        ("ticket_watchers", "ticket_ids", "user_ids", 5000),
        ("employee_skills", "employee_ids", None, 4000),
        ("employee_projects", "employee_ids", None, 3000),
        ("user_preferences", "user_ids", None, 12000),
        ("user_addresses_link", "user_ids", "address_ids", 10000),
        ("customer_consents", "customer_ids", None, 14000),
        ("category_translations", "category_ids", None, 200),
        ("product_translations", "product_ids", None, 8000),
    ]
    for name, left_key, right_key, n in junction_specs:
        left = CONTEXT.get(left_key, list(range(1, 1000)))
        right = CONTEXT.get(right_key, list(range(1, 1000))) if right_key else None
        tables.append(_gen_junction_table(name, left, right, n))
    # plus fillers
    for i in range(1, 35):
        tables.append(_gen_junction_table(f"link_table_{i:03d}", None, None,
                                           random.Random(SEED + 800 + i).randint(2000, 10000)))

    # 10 wide tables
    for i in range(1, 11):
        tables.append(_gen_wide_table(f"wide_report_{i:03d}",
                                       n_cols=random.Random(SEED + 900 + i).randint(40, 80),
                                       n_rows=random.Random(SEED + 900 + i).randint(100, 1500)))

    # 5 empty tables
    for i in range(1, 6):
        tables.append(_gen_empty_table(f"placeholder_{i:03d}"))

    return tables


# ----------------------------------------------------------------- driver

@contextmanager
def conn_cur(schema: str = "public"):
    conn = psycopg2.connect(**DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            conn.commit()
            yield conn, cur
    finally:
        conn.close()


def already_populated(cur, name: str) -> bool:
    cur.execute(f'SELECT EXISTS(SELECT 1 FROM "{name}" LIMIT 1)')
    return cur.fetchone()[0]


def run(reset: bool = False, schema: str = "public") -> None:
    t0 = time.time()
    tables = build_all_tables()
    print(f"Planned tables: {len(tables)}  (schema: {schema})")

    if reset:
        print(f"--reset specified: dropping all planned tables first from {schema!r}")
        with conn_cur(schema) as (conn, cur):
            for t in tables:
                cur.execute(f'DROP TABLE IF EXISTS "{t.name}" CASCADE')
            conn.commit()

    # Phase 1 — DDL only (idempotent CREATE IF NOT EXISTS)
    print(f"Phase 1/2: creating tables in schema {schema!r} (DDL only)...")
    with conn_cur(schema) as (conn, cur):
        for i, t in enumerate(tables, 1):
            cur.execute(t.ddl)
            if i % 50 == 0:
                conn.commit()
                print(f"  ddl progress: {i}/{len(tables)}", flush=True)
        conn.commit()
    print(f"  DDL done in {time.time()-t0:.1f}s")

    # Phase 2 — populate
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
                print(f"  generator error in {t.name}: {e}")
                continue
            if not rows:
                continue
            n = copy_rows(cur, t.name, t.column_names, rows)
            conn.commit()
            total_rows += n
            if idx % 25 == 0 or idx == len(tables):
                print(f"  [{idx:>3}/{len(tables)}] {t.name:<32s} +{n:>8} rows  total={total_rows:,}",
                      flush=True)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    with conn_cur(schema) as (conn, cur):
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=%s", (schema,))
        actual = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_schema=%s AND constraint_type='FOREIGN KEY'", (schema,))
        n_fks = cur.fetchone()[0]
    print(f"Tables in {schema}: {actual}")
    print(f"Total rows inserted (approx): {total_rows:,}")
    print(f"Foreign-key constraints in {schema}: {n_fks}  (must be 0)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="DROP all planned tables first (destructive)")
    ap.add_argument("--schema", default="public",
                    help="Target schema (default: public). The schema is created if absent.")
    args = ap.parse_args()
    run(reset=args.reset, schema=args.schema)
