# End-to-end example: 10 tables, 9 relationship patterns

A concrete walkthrough of one Archon-SuperNova job, phase by phase, on a
purpose-built schema that exercises every FK / PII / clustering codepath.
Read this alongside [`process.md`](process.md) — `process.md` describes
*what each phase does*; this document shows *what each phase actually emits*
for a worked input.

---

## 1. Source schema — `shop`

Ten tables, deliberately covering:

| # | Table              | Pattern demonstrated                                                       |
|---|--------------------|----------------------------------------------------------------------------|
| 1 | `customers`        | PII root (`email`, `phone`, `full_name`)                                   |
| 2 | `addresses`        | one-to-many → `customers`; PII inheritance via propagation                 |
| 3 | `orders`           | one-to-many → `customers`; low-cardinality FK → `order_statuses`           |
| 4 | `order_statuses`   | tiny lookup (8 rows) with text-coded PK — exercises low-card name bypass   |
| 5 | `products`         | **UUID-keyed PK** — exercises the structural-key FK-eligibility promotion  |
| 6 | `order_items`      | **composite FK** `(order_id, product_id)` — Phase 4b                       |
| 7 | `employees`        | **self-referential** `manager_id → id`; also a PII surface (`email`)        |
| 8 | `comments`         | **polymorphic** `commentable_type` + `commentable_id` — Phase 4c           |
| 9 | `events`           | **JSONB FK**: `payload.user_id → customers.id` — Phase 4d                  |
|10 | `employee_audits`  | leaf; PII inherited via propagation through `employees`                    |

Declared physical FKs (the ground truth Phase 5 should rediscover):

```
addresses.customer_id        → customers.id
orders.customer_id           → customers.id
orders.status_code           → order_statuses.code
order_items.order_id         → orders.id
order_items.product_id       → products.id
employees.manager_id         → employees.id
comments.author_id           → employees.id
employee_audits.employee_id  → employees.id
```

Logical / non-declared (the system must rediscover these without DDL hints):

```
comments.(commentable_type='order',   commentable_id) → orders.id        (polymorphic)
comments.(commentable_type='product', commentable_id) → products.id      (polymorphic)
events.payload->>'user_id'                            → customers.id     (jsonb)
```

Row-count profile (kept small so the trace is readable but realistic):

```
customers        2 000   addresses     5 200   orders          7 800
order_statuses       8   products      1 500   order_items   18 600
employees          150   comments     12 400   events         9 900
employee_audits  6 100
```

---

## 2. Phase-by-phase trace

The submitted job is `POST /api/jobs` with `schema=shop`. The API spawns
`python -m discovery run-all`; the orchestrator (`orchestrator.py`) drives
the 14 phases sequentially, recording per-phase status in `run_log`.

### Phase 1 — `inventory`

`inventory.py` reads `information_schema` via the extraction service and
upserts one row per table into `tbl_inventory` and one per column into
`col_inventory`. Per-column it computes:

- `type_class` (INT_WIDE / STRING_SHORT / STRING_LONG / UUID / JSONB / …)
- `is_pk`, `is_unique_indexed`, `is_indexed` from `pg_index`
- `is_fk_eligible` — `True` for INT/UUID/STRING_SHORT, plus the structural-key
  promotion: a column declared PK or named `id`/`<x>_id` keeps eligibility
  even when the type would normally exclude it (e.g. `products.id` is `UUID`
  → STRING_LONG-class but stays eligible because `is_pk=True`)

What lands in `col_inventory` for the interesting columns:

| column                          | type_class    | is_pk | is_fk_eligible | reason                          |
|---------------------------------|---------------|-------|----------------|---------------------------------|
| `customers.id`                  | INT_WIDE      | T     | T              | declared PK                     |
| `customers.email`               | STRING_SHORT  | F     | T              | default for varchar             |
| `products.id`                   | STRING_LONG   | T     | T              | **promoted** (is_pk override)   |
| `products.sku`                  | STRING_SHORT  | F     | T              |                                 |
| `order_items.order_id`          | INT_WIDE      | T     | T              | composite-PK member             |
| `order_statuses.code`           | STRING_SHORT  | T     | T              | declared PK                     |
| `events.payload`                | JSONB         | F     | F              | JSONB excluded; Phase 4d reads  |
| `comments.commentable_type`     | STRING_SHORT  | F     | T              | discriminator                   |
| `comments.commentable_id`       | INT_WIDE      | F     | T              | polymorphic key                 |

`run_log`: `inventory / global / 0 / succeeded`.

### Phase 2 — `extract`

`extraction.py` POSTs to the extraction service for each non-excluded table.
The service runs `COPY ... TO STDOUT` and writes a Parquet file under
`MOCK_STORAGE_PATH/<job_id>/<table>.parquet`. `tbl_inventory.parquet_path`
+ `parquet_bytes` are filled in. Only FK-eligible + PK + PII-eligible
columns are projected (saves disk).

Output: 10 Parquet files; total size ~12 MB for this profile.

### Phase 3a — `fingerprint`

`fingerprint.py` opens each Parquet, then per column computes:

- HyperMinHash sketch of the value set (pickled into `col_inventory.sketch_blob`)
- HLL `cardinality_estimate` and exact `distinct_count` when small
- `min_val` / `max_val` from `pyarrow` row-group statistics
- `null_pct`

After this phase, every FK-eligible column has a sketch we can
intersect / containment-test cheaply in Phase 4. Sample distinct counts:

```
customers.id          2 000     orders.id           7 800
products.id           1 500     order_items.order_id  7 800
order_items.product_id 1 500    employees.manager_id    24
order_statuses.code       8     events.payload          —  (JSONB skipped)
```

### Phase 3b — `pii_scan`

`pii_scan.py` samples up to 50 000 rows per text-class column, runs the
Hyperscan + regex pattern bank, the validators (Luhn, stdnum, etc.) and
the `name_prior` rules. Findings written to `pii_findings`:

| column                  | pii_type   | regex_match_rate | validated | score | name_prior |
|-------------------------|------------|------------------|-----------|-------|------------|
| `customers.email`       | EMAIL      | 1.00             | 1.00      | 0.99  | T          |
| `customers.phone`       | PHONE_US   | 0.92             | 0.88      | 0.95  | T          |
| `customers.full_name`   | PERSON     | —                | —         | 0.78  | T (via name) |
| `addresses.line1`       | ADDRESS    | 0.81             | —         | 0.74  | T          |
| `employees.email`       | EMAIL      | 1.00             | 1.00      | 0.99  | T          |
| `employee_audits.ip_address` | IPV4  | 1.00             | 1.00      | 0.99  | T          |
| `products.id`           | API_KEY    | 1.00             | —         | 0.71  | F          |

Note `products.id` — UUID-shaped values match the high-entropy `API_KEY`
pattern. This is a known false-positive of the regex layer; the
**structural-key suppression** in `run_phase_4` keeps it FK-eligible
anyway (declared PK), so it survives into candidate generation. The PII
finding itself is preserved for the report; only the FK-gating effect is
suppressed.

`run_log`: 32 column-scoped rows for `pii_scan`, all `succeeded`.

### Phase 4 — candidate generation (`candidates.py`)

Two passes both run; results merge into `fk_candidates`.

**4a · `sql_prefilter`** — quadratic in-memory self-join over
`col_inventory`. For each `(child, parent)` pair with compatible
type-classes, the gates apply in order:

1. parent has PK / unique signal (`require_parent_pk`)
2. cardinality compatibility (`child.distinct ≤ parent.distinct × 1.05`)
3. range overlap (`child.min/max ⊂ parent.min/max`)
4. type compatibility
5. cardinality floor with name-similarity / role-suffix bypass

Examples of what fires:

| pair                                       | gate path                                     | tier      |
|--------------------------------------------|-----------------------------------------------|-----------|
| `addresses.customer_id → customers.id`     | direct (name_sim=0.92, card 5.2k/2k OK)       | primary   |
| `orders.customer_id → customers.id`        | direct                                        | primary   |
| `orders.status_code → order_statuses.code` | low-card bypass (`name_sim` ≥ 0.85, parent PK) | primary  |
| `employees.manager_id → employees.id`      | self-ref role (manager → id)                   | primary   |
| `order_items.order_id → orders.id`         | direct                                        | primary   |
| `order_items.product_id → products.id`     | suffix-id-match (`product_id` token in `products`) | primary |
| `comments.author_id → employees.id`        | role-FK bypass (`author_*` → `employees.id`)   | primary   |
| `employee_audits.employee_id → employees.id` | direct + suffix-match                       | primary   |

Total prefilter: ~14 primary, ~80 advisory pairs.

**4b · `faiss_lsh_search`** — same gates but pairs come from the
HyperMinHash + LSH index keyed on the sketch bytes. This catches FK shapes
the SQL prefilter misses when names diverge but value-sets overlap.
For this schema it adds 3 more advisories and confirms the prefilter
primaries via second source.

After `dedup_bidirectional_candidates` + `filter_bridge_collisions` +
`apply_top_k_per_child`, `fk_candidates` holds the ranked set.

### Phase 5 — `validate`

`validate.py` promotes every `primary`-tier candidate to exact validation:
register both child and parent Parquets in DuckDB, run
`SELECT count(*) FROM child WHERE col NOT IN (SELECT pcol FROM parent)`
against actual values. Confidence ≥ 0.95 containment writes a row to
`relationships`.

| candidate                                  | containment | rows_unmatched | result        |
|--------------------------------------------|-------------|----------------|---------------|
| `addresses.customer_id → customers.id`     | 1.0000      | 0              | confirmed     |
| `orders.customer_id → customers.id`        | 1.0000      | 0              | confirmed     |
| `orders.status_code → order_statuses.code` | 1.0000      | 0              | confirmed     |
| `order_items.order_id → orders.id`         | 1.0000      | 0              | confirmed     |
| `order_items.product_id → products.id`     | 1.0000      | 0              | confirmed     |
| `employees.manager_id → employees.id`      | 0.9933      | 1 (CEO)        | confirmed     |
| `comments.author_id → employees.id`        | 1.0000      | 0              | confirmed     |
| `employee_audits.employee_id → employees.id` | 1.0000    | 0              | confirmed     |

8 single-column relationships persisted. The CEO (manager_id IS NULL)
explains the one mismatch — pipeline reports cardinality `many-to-one`
with `nullable=true`.

### Phase 4b · composite — `composite_fk.py`

Scans `fk_candidates` for tables where multiple confirmed FKs share a
target table; tests whether the cross-column tuple has containment in the
parent's PK tuple.

`order_items.(order_id, product_id)` is the only composite key in this
schema; both component FKs already passed Phase 5, so the composite is
emitted to `composite_relationships` with `containment=1.0`. Signals
the ER as a true junction, used by the clustering stage.

### Phase 4c · polymorphic — `polymorphic_fk.py`

Detects the `<entity>_type` + `<entity>_id` Rails/Django pattern. For
`comments`:

1. spots `commentable_type` (cardinality 2: `'order'`, `'product'`) +
   `commentable_id` (INT_WIDE)
2. for each value of `commentable_type`, partitions the child rows and
   tests containment of `commentable_id` against every candidate parent
   (`orders.id`, `products.id`, …)

Result, written to `polymorphic_relationships`:

```
comments.commentable_id  type='order'   → orders.id      (containment 1.00)
comments.commentable_id  type='product' → products.id    (containment 1.00)
```

### Phase 4d · jsonb — `jsonb_fk.py`

Flattens leaf paths from each JSONB column, rebuilds value sets, and
runs the same containment test against PK columns.

For `events.payload`:

- leaf paths discovered: `kind`, `user_id`, `device.os`, `device.app_ver`
- `user_id` (INT_WIDE) ⊂ `customers.id` with containment 1.0000
- the `device.*` paths fail name + containment gates → discarded

Result in `jsonb_relationships`:

```
events.payload->>'user_id' → customers.id   (containment 1.00, jsonpath '$.user_id')
```

### Phase · `inheritance`

Annotates IS-A patterns: a child whose PK is also a FK to another table's
PK. None present in `shop`. Run completes as no-op; `run_log` records
`inheritance / global / 0 / succeeded` with `metadata.candidates_found=0`.

### Phase · `pii_propagation`

Subject-rooted reverse BFS from every `pii_findings` root.

- root `customers` (subjects: PERSON, EMAIL, PHONE)
- BFS over the inverse FK graph (every confirmed FK in
  `relationships` + `composite_relationships` + polymorphic + jsonb)

```
customers ── (←addresses.customer_id)              addresses        d=1
customers ── (←orders.customer_id)                 orders           d=1
customers ── (←events.payload.user_id)             events           d=1
customers ── orders ── (←order_items.order_id)     order_items      d=2
customers ── orders ── (←comments via polymorphic) comments         d=2
```

`tbl_inventory.subject_kinds` updated to include `PERSON,EMAIL,PHONE` for
each downstream table; `subject_link_distance` records BFS depth.

`employees` is its own subject root (EMAIL on `employees.email`); the
walk reaches `comments` (via author_id) and `employee_audits` (via
employee_id), tagging both.

### Phase · `pii_leak`

For each PII column on a "subject" side, look for non-PII columns on the
non-subject side whose sketch has containment ≥ 0.5 with the PII
column. Catches accidental copies (e.g. `addresses.line1` value set
overlapping with a free-text `comments.body`). For this schema, no
leaks above the threshold — `pii_leaks` stays empty.

### Phase · `clustering` — `clustering.py`

1. Build the FK graph from `relationships_unified` (single + composite).
2. Identify and **collapse junctions**: `order_items` is a junction
   between `orders` and `products` (degree 2 + composite-PK on the FKs).
3. Classify archetypes per node:
   - `customers`, `products`, `employees`, `order_statuses` → DIM
   - `orders`, `comments`, `events` → FACT
   - `addresses`, `employee_audits` → AUDIT
   - `order_items` → JUNCTION (collapsed)
4. Weighted Louvain on the simplified graph.
5. Auto-name each cluster from the most-connected DIM/FACT.

Resulting `clusters`:

| cluster_id | name                  | members                                                                           |
|------------|-----------------------|-----------------------------------------------------------------------------------|
| 1          | `customer_orders`     | customers, addresses, orders, order_items, products, order_statuses, events       |
| 2          | `employee_audit`      | employees, comments, employee_audits                                              |

Cross-cluster bridges retained for the UI: `comments.author_id → employees.id`
sits in cluster 2; the polymorphic edge from `comments` to `orders` /
`products` shows up as a bridge to cluster 1.

### Phase · `report`

`report.py` materialises the per-schema report to
`reports/shop/`:

```
reports/shop/
├── relationships.csv          (8 single + 1 composite + 2 polymorphic + 1 jsonb)
├── pii_findings.csv           (7 column-level rows + propagated tables)
├── clusters.csv               (2 rows)
├── summary.md                 (one-page digest)
└── job.json                   (counts, durations, run_log digest)
```

`run_log`: `report / global / 0 / succeeded`. The job's row in `jobs` is
flipped to `status='succeeded'` with `relationships_count=12`,
`pii_count=12`, `cluster_count=2`.

---

## 3. What the UI shows

The Angular UI fetches via `/api/jobs/<id>/...`:

- **Dashboard**: `shop` card shows tables=10, relationships=12, PII=12, clusters=2.
- **Job detail / Clusters tab**: two cluster cards with table-count badges
  and an expand-to-cluster-graph link.
- **Relationships tab**: vis-network graph; nodes tinted by cluster.
  Edges have crow's-foot endpoints, edge thickness proportional to
  Phase-5 containment. Polymorphic + JSONB edges rendered with a dashed
  style; the polymorphic edge label reads
  `commentable_id [type=order|product]`.
- **PII tab**: 7 column rows (the regex/validator findings); the
  propagation column lists `customers → addresses, orders, events,
  order_items, comments` (transitively).
- **Cluster detail**: per-cluster ERD (`erd-card`) with cross-cluster
  bridge cards rendered in the *originating* cluster's tint.

---

## 4. End-to-end timings (illustrative)

For this 10-table / ~63 k-row profile on a single 8-core dev box:

| phase           | wall time |
|-----------------|-----------|
| inventory       | 0.2 s     |
| extract         | 1.4 s     |
| fingerprint     | 1.8 s     |
| pii_scan        | 3.1 s     |
| candidate_gen   | 0.3 s     |
| validate        | 1.6 s     |
| composite_fk    | 0.1 s     |
| polymorphic_fk  | 0.2 s     |
| jsonb_fk        | 0.4 s     |
| inheritance     | 0.0 s     |
| pii_propagation | 0.1 s     |
| pii_leak        | 0.5 s     |
| clustering      | 0.2 s     |
| report          | 0.2 s     |
| **total**       | **~10 s** |

Larger schemas keep this profile shape: extract + pii_scan + validate
dominate; everything else is <5 % of total.

---

## 5. Key takeaways from this example

1. **Structural keys survive PII over-tagging.** `products.id` is a UUID
   that the regex layer flags as `API_KEY`; because it's declared PK the
   pipeline keeps it FK-eligible, and Phase 5 confirms
   `order_items.product_id → products.id`. Without that promotion the
   entire UUID-keyed half of the schema would be invisible to FK
   discovery.
2. **Low-cardinality FKs need a name bypass.** `orders.status_code` (8
   distinct) only survives because lexical name similarity to
   `order_statuses.code` is ≥ 0.85 and the parent has a PK signal.
3. **Polymorphic + JSONB live in their own phases.** Phase 5 only sees
   single-column physical FK candidates; the polymorphic and JSONB
   relationships are emitted by separate phases (4c / 4d) into separate
   tables and unioned for the UI.
4. **PII propagation is graph-driven.** A column-level finding on
   `customers.email` is enough for *every* downstream table reachable
   through any kind of relationship (single, composite, polymorphic,
   jsonb) to inherit the subject tag.
5. **Clustering uses the unified graph.** Junction collapse + archetype
   classification means `order_items` doesn't fragment the customer-orders
   cluster, and `comments` ends up grouped with employees rather than
   with the orders side it polymorphically references.
