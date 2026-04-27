"""
Seed a small Rails-style polymorphic schema into Postgres ``poly``.

Tables (~6, declared PKs only -- no FK constraints; the discovery pipeline
must rediscover the relationships):

    posts(id PK, title, body, created_at)
    articles(id PK, headline, body, published_at)
    users(id PK, email, name)

    comments(id PK, commentable_type, commentable_id, body, author_id, created_at)
        commentable_type IN ('Post','Article')
        commentable_id   -> posts.id  when type='Post'
        commentable_id   -> articles.id when type='Article'
        author_id        -> users.id

    attachments(id PK, attachable_type, attachable_id, filename, size_bytes)
        attachable_type IN ('Post','Article','Comment')

    events(id PK, payload jsonb, occurred_at)
        payload -> JSON object
        events.payload->>'order_id'  -> orders.id (FK-shaped)
        events.payload->>'user_id'   -> users.id  (FK-shaped)

    orders(id PK, customer_id, total_amount, created_at)
        customer_id      -> users.id  (note: column intentionally named
                          customer_id even though parent is users -- gives
                          the regular FK detector a tiny bit to chew on)

The synthetic data is seeded with realistic value distributions so that
discovery's containment metrics surface the relationships.

Usage:
    python3 seed_postgres_polymorphic.py
    python3 seed_postgres_polymorphic.py --reset
    python3 seed_postgres_polymorphic.py --schema poly --reset
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Re-use psycopg + helpers from the 500-table seed script.
sys.path.insert(0, str(Path(__file__).parent))

import psycopg2

DSN = dict(
    host="localhost",
    port=5432,
    dbname="test",
    user="adsuser",
    password="Ads@3421",
    connect_timeout=10,
)
SEED = 42
random.seed(SEED)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def conn_cur(schema: str = "poly"):
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


def _table_exists(cur, schema: str, name: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        )
        """,
        (schema, name),
    )
    return bool(cur.fetchone()[0])


def _row_count(cur, name: str) -> int:
    cur.execute(f'SELECT COUNT(*) FROM "{name}"')
    return int(cur.fetchone()[0])


def _drop_all(cur, names: list[str]) -> None:
    for n in names:
        cur.execute(f'DROP TABLE IF EXISTS "{n}" CASCADE')


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY,
        email         VARCHAR(120) NOT NULL,
        name          VARCHAR(120) NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS posts (
        id            INTEGER PRIMARY KEY,
        title         VARCHAR(200) NOT NULL,
        body          TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS articles (
        id            INTEGER PRIMARY KEY,
        headline      VARCHAR(200) NOT NULL,
        body          TEXT NOT NULL,
        published_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id              INTEGER PRIMARY KEY,
        customer_id     INTEGER NOT NULL,
        total_amount    NUMERIC(12, 2) NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comments (
        id                 INTEGER PRIMARY KEY,
        commentable_type   VARCHAR(40) NOT NULL,
        commentable_id     INTEGER NOT NULL,
        body               TEXT NOT NULL,
        author_id          INTEGER NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attachments (
        id                 INTEGER PRIMARY KEY,
        attachable_type    VARCHAR(40) NOT NULL,
        attachable_id      INTEGER NOT NULL,
        filename           VARCHAR(255) NOT NULL,
        size_bytes         BIGINT NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id              INTEGER PRIMARY KEY,
        payload         JSONB NOT NULL,
        occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]

ALL_TABLES = [
    "users", "posts", "articles", "orders",
    "comments", "attachments", "events",
]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def _gen_users(rng: random.Random) -> list[tuple]:
    rows = []
    for i in range(1, 101):
        rows.append((
            i,
            f"user{i}@example.com",
            f"User {i}",
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
        ))
    return rows


def _gen_posts(rng: random.Random) -> list[tuple]:
    rows = []
    for i in range(1, 51):
        rows.append((
            i,
            f"Post {i} Title",
            f"Body of post {i} -- " + ("lorem ipsum " * rng.randint(2, 8)),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
        ))
    return rows


def _gen_articles(rng: random.Random) -> list[tuple]:
    rows = []
    for i in range(1, 31):
        rows.append((
            i,
            f"Article {i} Headline",
            f"Article body {i} -- " + ("dolor sit amet " * rng.randint(3, 10)),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
        ))
    return rows


def _gen_orders(rng: random.Random, user_ids: list[int]) -> list[tuple]:
    rows = []
    for i in range(1, 201):
        rows.append((
            i,
            rng.choice(user_ids),
            round(rng.uniform(5.0, 5000.0), 2),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
        ))
    return rows


def _gen_comments(
    rng: random.Random,
    user_ids: list[int],
    post_ids: list[int],
    article_ids: list[int],
) -> list[tuple]:
    rows = []
    cid = 1
    # 200 comments split between Post (60%) and Article (40%).  Containment
    # 1.0 against the appropriate parent is the proof signal.
    for _ in range(200):
        if rng.random() < 0.6:
            ctype = "Post"
            target = rng.choice(post_ids)
        else:
            ctype = "Article"
            target = rng.choice(article_ids)
        rows.append((
            cid,
            ctype,
            target,
            f"Comment body {cid}",
            rng.choice(user_ids),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 90)),
        ))
        cid += 1
    return rows


def _gen_attachments(
    rng: random.Random,
    post_ids: list[int],
    article_ids: list[int],
    comment_ids: list[int],
) -> list[tuple]:
    rows = []
    aid = 1
    for _ in range(150):
        kind = rng.choice(["Post", "Article", "Comment"])
        if kind == "Post":
            target = rng.choice(post_ids)
        elif kind == "Article":
            target = rng.choice(article_ids)
        else:
            target = rng.choice(comment_ids)
        rows.append((
            aid,
            kind,
            target,
            f"file_{aid}.pdf",
            rng.randint(1024, 1024 * 1024 * 10),
        ))
        aid += 1
    return rows


def _gen_events(
    rng: random.Random,
    user_ids: list[int],
    order_ids: list[int],
) -> list[tuple]:
    """Each event payload carries either an order_id or a user_id leaf path,
    sometimes both -- so the JSONB detector can find both relationships.
    """
    rows = []
    for i in range(1, 401):
        # 70% of events reference an order_id, 50% reference a user_id;
        # ~30% reference both.
        payload: dict = {"event_type": rng.choice(["click", "view", "purchase"])}
        if rng.random() < 0.7:
            payload["order_id"] = rng.choice(order_ids)
        if rng.random() < 0.5:
            # Nest one to exercise the detector with a deeper path:
            # $.actor.user_id (and $.user_id) both appear.
            if rng.random() < 0.5:
                payload["actor"] = {"user_id": rng.choice(user_ids)}
            else:
                payload["user_id"] = rng.choice(user_ids)
        rows.append((
            i,
            json.dumps(payload),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 90)),
        ))
    return rows


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run(reset: bool = False, schema: str = "poly") -> None:
    t0 = time.time()
    print(f"Polymorphic seed -- target schema: {schema}")

    if reset:
        with conn_cur(schema) as (conn, cur):
            print(f"  resetting -- DROP {len(ALL_TABLES)} tables")
            _drop_all(cur, ALL_TABLES)
            conn.commit()

    # DDL.
    with conn_cur(schema) as (conn, cur):
        for ddl in DDL_STATEMENTS:
            cur.execute(ddl)
        conn.commit()

    rng = random.Random(SEED)
    user_rows = _gen_users(rng)
    post_rows = _gen_posts(rng)
    article_rows = _gen_articles(rng)
    user_ids = [r[0] for r in user_rows]
    post_ids = [r[0] for r in post_rows]
    article_ids = [r[0] for r in article_rows]

    order_rows = _gen_orders(rng, user_ids)
    order_ids = [r[0] for r in order_rows]
    comment_rows = _gen_comments(rng, user_ids, post_ids, article_ids)
    comment_ids = [r[0] for r in comment_rows]
    attachment_rows = _gen_attachments(rng, post_ids, article_ids, comment_ids)
    event_rows = _gen_events(rng, user_ids, order_ids)

    inserts: list[tuple[str, list[str], list[tuple]]] = [
        ("users", ["id", "email", "name", "created_at"], user_rows),
        ("posts", ["id", "title", "body", "created_at"], post_rows),
        ("articles", ["id", "headline", "body", "published_at"], article_rows),
        (
            "orders",
            ["id", "customer_id", "total_amount", "created_at"],
            order_rows,
        ),
        (
            "comments",
            [
                "id", "commentable_type", "commentable_id", "body",
                "author_id", "created_at",
            ],
            comment_rows,
        ),
        (
            "attachments",
            [
                "id", "attachable_type", "attachable_id", "filename",
                "size_bytes",
            ],
            attachment_rows,
        ),
        ("events", ["id", "payload", "occurred_at"], event_rows),
    ]

    with conn_cur(schema) as (conn, cur):
        for tbl, cols, rows in inserts:
            existing = _row_count(cur, tbl)
            if existing > 0:
                print(f"  {tbl}: already populated ({existing} rows) -- skip")
                continue
            placeholders = ",".join(["%s"] * len(cols))
            colnames = ",".join(f'"{c}"' for c in cols)
            stmt = f'INSERT INTO "{tbl}" ({colnames}) VALUES ({placeholders})'
            cur.executemany(stmt, rows)
            print(f"  {tbl}: inserted {len(rows)} rows")
        conn.commit()

    print(f"Done in {time.time()-t0:.1f}s")


def main() -> int:
    p = argparse.ArgumentParser(description="Seed Rails-style polymorphic schema")
    p.add_argument("--schema", default="poly", help="target schema name")
    p.add_argument(
        "--reset", action="store_true",
        help="DROP all tables before recreating (LOSES DATA)",
    )
    args = p.parse_args()
    run(reset=args.reset, schema=args.schema)
    return 0


if __name__ == "__main__":
    sys.exit(main())
