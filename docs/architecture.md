# Architecture

Archon-SuperNova is a **data-modeling / archival product** that crawls
relational databases (today: PostgreSQL), rediscovers undeclared foreign-key
relationships, classifies columns for PII, clusters tables into communities,
and exposes the results through a REST API + web UI.

The repository is organised as:

```
archon-supernova/
├── backend/
│   ├── python/         # FastAPI + discovery pipeline + mock extraction
│   │   ├── api/        # FastAPI app (uvicorn entrypoint: main.py)
│   │   ├── pipeline/   # discovery package + SQL DDL + tests
│   │   └── mock_extraction_service.py
│   └── java/           # Spring Boot extraction service stub
├── frontend/
│   └── ui/             # Angular 17 SPA (Archon-SuperNova)
├── scripts/
│   ├── start.sh        # boots the full stack
│   └── stop.sh
└── docs/               # this folder
```

This document describes every component in the system and how they fit
together.

```
                                          ┌──────────────────┐
                                          │   Source DB(s)   │
                                          │   (Postgres)     │
                                          └────────┬─────────┘
                                                   │ COPY
                                                   ▼
┌──────────────┐   GET /extract   ┌─────────────────────────┐
│ Pipeline     │ ───────────────► │  Extraction service     │
│ (Python)     │ ◄─────────────── │  (mock_extraction or    │
│              │   parquet on FS  │   Java Spring stub)     │
└──────┬───────┘                  └─────────────────────────┘
       │ writes results to
       ▼
┌─────────────────────────────────────────────────────────────┐
│                 discovery_results.discovery                 │
│ (Postgres — the persistent store for everything we find)    │
│  jobs · tbl_inventory · col_inventory · run_log ·           │
│  fk_candidates · relationships · composite/polymorphic/     │
│  jsonb relationships · pii_findings · pii_leaks · clusters  │
└──────────┬──────────────────────────────────────────────────┘
           │ reads
           ▼
┌──────────────────────────┐ HTTP    ┌─────────────────────┐
│  FastAPI (uvicorn)       │ ◄────── │  Angular UI         │
│  backend/python/api/     │         │  frontend/ui/src    │
│  main.py — port 8000     │         │  port 4200          │
└──────────────────────────┘         └─────────────────────┘
```

## Components

### 1. Pipeline — `pipeline/`

The discovery engine. Pure Python 3.11, no microservices. Drives one job
through 14 phases (see [process.md](process.md)). Entry: `python -m discovery
<sub-command>` via `backend/python/pipeline/src/discovery/cli.py`.

Key modules under `backend/python/pipeline/src/discovery/`:

| Module | Role |
|---|---|
| `cli.py` | Typer CLI — `init`, `inventory`, `extract`, `fingerprint`, `pii-scan`, `generate-candidates`, `validate`, `composite-candidates`, `polymorphic-candidates`, `jsonb-candidates`, `annotate-inheritance`, `propagate-pii`, `leak-scan`, `cluster`, `report`, `run-all` |
| `orchestrator.py` | `run_all()` — drives the 14-phase sequence, handles `run_log` resume semantics |
| `inventory.py` | Phase 1 — enumerate tables/columns from `information_schema` |
| `extraction.py` / `extraction_client.py` | Phase 2 — POST to extraction service, store Parquet on disk |
| `fingerprint.py` | Phase 3a — HyperMinHash sketches + HLL cardinality + min/max per column |
| `pii_scan.py` | Phase 3b — regex + name-prior + Luhn/stdnum + spaCy NER |
| `pii_patterns.py` / `pii_priors.py` / `pii_score.py` / `pii_locale.py` / `pii_ner.py` | PII rule packs and Bayesian scoring |
| `pii_propagation.py` | Subject-rooted reverse-BFS — tags every table reachable from a PII root |
| `pii_leak.py` | Cross-cluster sketch-based leak detector — `containment ≥ 0.5` between PII columns and non-PII targets |
| `candidates.py` | Phase 4 — SQL pre-filter + FAISS LSH candidate generation, reverse-direction dedup, bridge-collision filter, range-overlap penalty, role-FK bypass |
| `name_similarity.py` | Lexical + optional sentence-transformers semantic name match |
| `scoring.py` | Composite confidence score (containment + name + parent-PK + cardinality + jaccard) |
| `validate.py` | Phase 5 — DuckDB exact containment + cardinality classification on the actual Parquet data |
| `composite_fk.py` | Phase 4b — multi-column FK detection |
| `polymorphic_fk.py` | Phase 4c — Rails/Django `entity_type`+`entity_id` pattern detector |
| `jsonb_fk.py` | Phase 4d — extract leaf paths from `jsonb` columns and test containment |
| `inheritance.py` | Post-Phase-5 evidence annotator for IS-A patterns |
| `clustering.py` | Junction-collapse, node typology (FACT / DIMENSION / LOOKUP / JUNCTION / AUDIT / EMPTY), weighted Louvain on the FK graph, cluster auto-naming. Zero-record tables are pulled out of Louvain and grouped under a single `<schema>_empty_tables` cluster |
| `results_db.py` | SQLAlchemy Core schema for the `discovery_results.discovery` Postgres tables + DAO objects (one DAO per result type) |
| `run_log.py` | Per-phase audit log with `start/succeed/fail` + `is_complete` resume guard |
| `report.py` | Phase 7 — emits `csv`/`xlsx` artifacts under `reports/<schema>/` |
| `config.py` | Pydantic config model. Env-var interpolation in YAML via `${VAR:default}` |
| `models.py` / `type_class.py` | Shared dataclasses + the type-class lattice (INT / STRING / DATE / UUID / JSONB / ...) |

DDL in `backend/python/pipeline/sql/results_schema.sql` — this is the single
source of truth for the result-DB shape; `discovery init` runs it.

#### Recent precision improvements

These are baked into the modules above; calling them out here so the
architecture map matches what the code actually does today.

- **Structural-key FK eligibility (`inventory.py`).** A column declared
  `PRIMARY KEY` / `UNIQUE`, or whose name is `id` / `<x>_id`, keeps
  `is_fk_eligible=true` even when its `TypeClass` is `STRING_LONG` (the
  default exclusion for long-text columns). Critical for UUID/text-keyed
  schemas — without it, every FK out of a UUID PK is invisible to
  candidate generation.
- **Suffix-id-match candidate gate (`scoring.py` + `candidates.py`).**
  Generic `<x>_id → <table>.id` rule with token-overlap matching plus
  singularisation. Recovers FKs whose lexical similarity is below 0.85
  but whose name conforms to the universal SQL convention.
- **PII pointer suppression (`pii_priors.py` + `pii_scan.py`).**
  Findings on pointer-named columns (`id` / `*_id` / `*_uuid` / `*_pk`
  / `*_fk` / `*_ref`) are dropped unless the column name has a positive
  prior for the matched type. UUID values triggering `API_KEY` /
  `PHONE_US` regex hits no longer pollute the findings table.
- **Empty-table cluster (`clustering.py`).** Tables with
  `row_count_estimate = 0` are pulled out of the Louvain input and
  emitted as one labelled cluster `<schema>_empty_tables`, archetype
  `EMPTY`. Replaces the long tail of meaningless singleton clusters.

### 2. API — `backend/python/api/main.py`

FastAPI app, single file (~1600 LoC). Boots via `uvicorn main:app --port 8000`.
Required env: `SOURCE_DB_PASSWORD`, `RESULTS_DB_PASSWORD`, `DISCOVERY_API_TOKEN`.

Responsibilities:
- HTTP surface for the UI (see [api.md](api.md))
- In-memory `_jobs` registry **with write-through to `discovery.jobs`** so
  job history survives backend restarts
- Background runner: `submit_job` spawns `python -m discovery run-all` as a
  subprocess; status updates persisted on every transition
- Defensive crash-recovery: jobs left as `running` at boot are surfaced as
  `failed` with reason "API restarted while job was in flight"
- Source-DB credential pipelined per-request: the form password becomes
  `password_inline` on the `ConnectionConfig` sent to the extractor. The
  process-wide `SOURCE_DB_PASSWORD` env var is now only a CLI fallback
- Pre-submit connection probe: `POST /api/test_connection` runs
  `psycopg2.connect → SELECT version() → schema-existence check → BASE
  TABLE count`. The UI's **Run discovery** button is disabled until this
  succeeds for the current field values
- Structured run-log: `GET /api/jobs/{id}/run_log` returns a per-phase
  rollup of `discovery.run_log`; `/log` strips ANSI before returning
  the raw subprocess output
- Auth: `POST /api/jobs` and `POST /api/test_connection` require
  `X-Discovery-Token`; GET endpoints open
- CORS: `localhost:4200` and `127.0.0.1:4200` only

### 3. UI — `frontend/ui/`

Standalone Angular 17 SPA. Built with the Angular CLI (`ng serve --proxy-config
proxy.conf.json`). Proxies `/api/*` → `http://127.0.0.1:8000`. Components:

| Component | Path | Purpose |
|---|---|---|
| `dashboard` | `components/dashboard/` | Cross-schema landing — one card per seeded source schema with table/relationship/PII/cluster counts and a "Run all" submit |
| `job-submit` | `components/job-submit/` | The `/submit` form (host/port/db/user/password/schema/label) |
| `job-list` | `components/job-list/` | Job history table with poll on running |
| `job-detail` | `components/job-detail/` | Tabs: **Clusters** (default), Relationships graph, PII findings, Run log. Shared header with `app-export-bar` |
| `cluster-overview` | `components/cluster-overview/` | Cluster cards grid + segmented "Cards / Cluster graph" toggle |
| `cluster-graph` | `components/cluster-graph/` | vis-network columnar layout — one column per cluster, all tables visible, fixed positions, edges between columns |
| `cluster-detail` | `components/cluster-detail/` | Per-cluster page: table list, intra-cluster edges, PII findings, **per-cluster ERD** with cross-cluster bridge cards colored by their owning cluster |
| `relationship-graph` | `components/relationship-graph/` | Force-directed vis-network, every node tinted by cluster, crow's-foot endpoints, hierarchical layout toggle, confidence slider with live tier counts |
| `erd-card` | `components/erd-card/` | dbdiagram.io-style ERD: cards with column rows, SVG cubic-bezier edges from FK column to PK column. Reused by `cluster-detail` with `[filterTableNames]` + `[bridgeTableNames]` |
| `pii-table` | `components/pii-table/` | Sortable findings table |
| `table-detail` | `components/table-detail/` | Click a node in the graph → expands inline table below with Columns / FK in / FK out / PII tables |
| `export-bar` | `components/export-bar/` | DBML / Mermaid / JSON download buttons |

Service: `services/job.service.ts` wraps every `/api/*` call. Models in
`models/job.model.ts`.

### 4. Mock extraction service — `backend/python/mock_extraction_service.py`

Single-file Python stdlib HTTP server (no Flask/FastAPI). Listens on port
8080, accepts `POST /api/v1/extract` with a connection config + SQL query +
output path. Connects to Postgres via `psycopg2`, runs `COPY ... TO STDOUT`,
encodes the CSV stream as Arrow + writes a Parquet file. Bearer-token auth
via `EXTRACTION_SERVICE_TOKEN`. Used in dev / CI; production swaps in the
Java implementation below.

### 5. Java extraction service stub — `backend/java/`

Spring Boot project (Java 17, Maven) that mirrors the OpenAPI contract in
`openapi/`. **Currently NOT wired into any default workflow**; the mock
service handles every test case in this codebase. Keep it as a parallel
"production" implementation that has full JSqlParser query whitelisting,
source-DB throttling, and the security filter chain the tests cover.

### 6. Synthetic data — `synthetic-data/`

Fixture seeders for testing. Each script seeds one schema in a Postgres
database with declared PKs (and selectively no FKs, so the pipeline must
rediscover them).

| Script | Schema | Profile |
|---|---|---|
| `seed_postgres_500.py` | `public` | 500 mostly-disconnected tables, smoke-test |
| `seed_postgres_500_e_commerce.py` | `public2` | Real e-commerce shape, 25 expected FKs |
| `seed_postgres_hr.py` | `hr` | HR domain, 88 expected FKs |
| `seed_postgres_adv.py` | `adv` | AdventureWorks shape, 54 expected FKs |
| `seed_postgres_dvdrental.py` | `dvdrental` | Pagila shape, 22 expected FKs |
| `seed_postgres_saleor.py` | `saleor` | UUID-PK e-commerce (Saleor), 43 expected FKs |
| `seed_postgres_polymorphic.py` | `poly` | Rails-style `commentable_type`/`commentable_id` polymorphism + a `jsonb` events column |

### 7. Result DB

Postgres database `discovery_results`, schema `discovery`. Created/migrated
by `discovery init`. Tables (see `pipeline/sql/results_schema.sql`):

| Table | What it stores |
|---|---|
| `jobs` | API-side job metadata (label, schema, status, timing, counts, source DSN, work_dir/cfg/log paths) |
| `run_log` | Per-phase audit (`phase, scope_type, scope_id, status, started_at, ended_at`) |
| `tbl_inventory` | Tables seen + row count + cluster_id + archetype + subject_kinds (PII propagation) |
| `col_inventory` | Columns + type class + PK/unique flags + HyperMinHash sketch blob + min/max + cardinality |
| `fk_candidates` | Phase-4 candidates (primary / advisory tier) |
| `relationships` | Phase-5 confirmed FKs (containment ≥ 0.95). Has `evidence` JSONB and `direction_reason` |
| `composite_relationships` | Multi-column FKs |
| `polymorphic_relationships` | Rails/Django polymorphic associations |
| `jsonb_relationships` | FK-shaped values inside JSONB columns |
| `pii_findings` | Per-column PII tags (column-level) and table-level IDENTITY_BUNDLE rows |
| `pii_leaks` | Cross-cluster value-set overlaps between PII and non-PII columns |
| `clusters` | One row per cluster with `member_table_ids` JSONB, modularity, archetype distribution |
| `relationships_unified` | View that UNIONs single + composite relationships for the API |

### 8. Tooling

| File | Purpose |
|---|---|
| `scripts/start.sh` | Launches mock + uvicorn + ng serve (idempotent, persists PIDs to `/tmp/archon-stack.pids`, waits for HTTP readiness) |
| `scripts/stop.sh` | Reads the PID file + sends SIGTERM with SIGKILL fallback, defensive `pkill` sweep, port-release verification |
| `docs/openapi/` | Extraction-service OpenAPI contract — single source of truth for both the Java impl and the Python mock |

## Cross-cutting choices

- **Recall-first defaults.** Every confidence/threshold knob is tuned to
  catch real FKs first; precision is recovered downstream by tier filters
  (`validate_only_primary_tier`) and the cluster engine.
- **Idempotent / resumable.** `run_log` lets every phase no-op when its
  scope is already `succeeded`. Re-running the same job is safe.
- **Sketch + exact two-pass.** Phases 3–4 work on HyperMinHash + LSH
  estimates for cheap candidate generation; Phase 5 promotes survivors
  to exact DuckDB containment over the Parquet.
- **Three pluggable layers.** Source DB (any Postgres), extraction
  service (Python mock or Java prod), result DB (Postgres). Each is
  reachable via env-overridable connection config.
