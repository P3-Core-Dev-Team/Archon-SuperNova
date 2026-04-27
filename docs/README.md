# Archon-SuperNova

**Undeclared single-column foreign-key discovery and PII detection across large Postgres databases — with a UI for submission, monitoring, exploration, and export.**

> Companion docs:
> [`architecture.md`](architecture.md) — component map, repo layout, design choices.
> [`process.md`](process.md) — one-job trace, phase by phase.
> [`api.md`](api.md) — every REST endpoint.
> [`example.md`](example.md) — worked example on a 10-table schema.
> [`openapi/`](openapi/) — extraction-service OpenAPI contract.

## What it does

Given a relational database, Archon-SuperNova:

1. **Inventories** every table and column (PK / unique / index metadata, type class, distinct counts).
2. **Extracts** the projected columns to local Parquet via a separate extraction service.
3. **Fingerprints** every column with a HyperMinHash sketch + HLL cardinality + min/max + null-pct.
4. **Detects PII** with regex / Hyperscan + validators (Luhn, stdnum) + name priors + spaCy NER.
5. **Discovers undeclared FKs** through a 4-stage candidate generator (single, composite, polymorphic, JSONB) followed by exact DuckDB containment validation.
6. **Annotates inheritance**, **propagates PII** along the FK graph, and detects **value leaks**.
7. **Clusters** the resulting graph (junction collapse + node typology + weighted Louvain) and **reports** to CSV / Excel.

The UI shows the result as a dashboard, a relationship graph, an ERD-card view, a per-cluster page, a PII findings table, and a structured run-log.

## Repository layout

```
archon-supernova/
├── README.md                       # top-level (this is docs/README.md, the deeper one)
├── .gitignore
│
├── backend/
│   ├── python/
│   │   ├── api/
│   │   │   └── main.py              # FastAPI app — uvicorn entrypoint
│   │   ├── pipeline/
│   │   │   ├── pyproject.toml
│   │   │   ├── config/default.yaml
│   │   │   ├── sql/results_schema.sql
│   │   │   ├── src/discovery/       # 14-phase pipeline package
│   │   │   └── tests/               # pytest unit + integration
│   │   ├── synthetic-data/          # seeders for 7 test schemas
│   │   └── mock_extraction_service.py
│   │
│   └── java/
│       └── extraction-service/      # Spring Boot 3.2 stub (mirrors openapi)
│
├── frontend/
│   └── ui/                          # Angular 17 SPA — Archon-SuperNova
│
├── scripts/
│   ├── start.sh                     # boots the full stack (idempotent)
│   └── stop.sh
│
└── docs/
    ├── README.md                    # this file
    ├── architecture.md
    ├── process.md
    ├── api.md
    ├── example.md
    └── openapi/extraction-service-v1.yaml
```

## Quick start (development)

The stack is three processes orchestrated by `scripts/start.sh`. You need:

- Postgres 14+ with two databases: a **source** DB (any schema you want to scan) and a **results** DB called `discovery_results` (created automatically on first init).
- Python 3.10+ with the pipeline's deps installed (`pip install -e backend/python/pipeline`).
- Node 20 (managed via `nvm`) for the Angular UI.

```bash
# from the repo root
bash scripts/start.sh
```

`start.sh` is idempotent — it kills any prior stack instance before starting. It prints PIDs, log paths, and waits for HTTP readiness on every service:

```
[start] mock_extraction_service on :8080
[start] uvicorn (FastAPI) on :8000
[start] ng serve on :4200
[start] waiting for services to come up...
[start]   mock_extraction on :8080 — ready (1s)
[start]   uvicorn on :8000 — ready (0s)
[start]   ng serve on :4200 — ready (3s)
[start]   /api/health OK
[start]   ng index.html OK

✓ Archon-SuperNova stack is up:
   Mock extraction : http://127.0.0.1:8080  (log: /tmp/mock.log)
   FastAPI         : http://127.0.0.1:8000  (log: /tmp/api.log)
   Angular UI      : http://localhost:4200   (log: /tmp/ng.log)
```

Open **http://localhost:4200** to use the UI; stop with `bash scripts/stop.sh`.

### Environment variables

`scripts/start.sh` exports sensible defaults. Override by `export`ing before invocation:

| Var | Default | Purpose |
|---|---|---|
| `SOURCE_DB_PASSWORD` | `Ads@3421` | Fallback only — the UI form's password is now the primary channel |
| `RESULTS_DB_PASSWORD` | `Ads@3421` | Password for `discovery_results` |
| `DISCOVERY_API_TOKEN` | `dev-secret` | Required header on `POST /api/jobs` and `POST /api/test_connection` |
| `EXTRACTION_SERVICE_TOKEN` | `dev-token` | Mock service bearer-token check |
| `MOCK_PORT` / `API_PORT` / `UI_PORT` | `8080` / `8000` / `4200` | Listen ports |
| `MOCK_STORAGE_PATH` | `/tmp/archon-parquet` | Where the mock writes Parquet |

## Submitting a job (UI workflow)

The Submit form has **two action buttons**:

1. **Test connection** (always enabled when every required field is filled). Probes the source DB: `psycopg2.connect(timeout=5)` → `SELECT version()` → schema-existence check → `BASE TABLE` count. Renders a green banner with `server_version`, `current_user`, and `table_count` on success, or a red banner with the underlying error (`connect failed`, `schema "x" not found`, etc.) on failure.
2. **Run discovery** — disabled until **Test connection** succeeds for the *current* field values. Any edit to host / port / database / user / password / schema invalidates the prior result and re-disables Run.

On Run, the API spawns `python -m discovery run-all` as a daemon-thread subprocess and streams a 14-phase pipeline. The browser is redirected to `/jobs/<job_id>` where the status polls every 3s.

The form-supplied password rides on each extraction request as `password_inline` on the `ConnectionConfig` (see [api.md → "Source-DB credentials"](api.md#source-db-credentials)). It is never logged.

## Job-detail tabs

| Tab | What it renders |
|---|---|
| **Clusters** *(default)* | Cluster cards grid + a "Cluster graph" toggle showing one column per cluster, columnar layout, edges between columns. Empty (zero-record) tables collapse into one labelled cluster `<schema>_empty_tables` with archetype `EMPTY`. |
| **Relationships graph** | Force-directed vis-network. Nodes tinted by cluster. Crow's-foot endpoints; edge thickness ∝ Phase-5 containment. Click a node → table-detail panel slides in below with Columns / FK in / FK out / PII tabs. |
| **PII findings** | Sortable findings table. Columns whose name is a *structural pointer* (`id` / `*_id` / `*_uuid` / `*_pk` / `*_fk` / `*_ref`) are no longer cluttered with `API_KEY` / `PHONE_US` false positives — those are suppressed unless the column name actually implies the matched PII type. |
| **Run log** | Two stacked panels: a structured per-phase audit (phase, scope, status, sub-tasks, started, duration, error) + a plain-text raw log (ANSI escapes stripped). Failed phase rows tinted; status pills colour-coded. |

Plus an **ERD card view** link in the meta bar, opening a dbdiagram.io-style cards-and-edges visualisation with cross-cluster bridge cards.

## What changed recently

Surface-level user-visible deltas (everything has a corresponding entry in `git log`):

- **Test-connection gate** (`a038616`) — `POST /api/test_connection` + UI Test-connection button. Run-discovery is disabled until the test succeeds.
- **Run-log tab now actually shows something** (`90b391c`) — `/log` strips ANSI; new `/api/jobs/{id}/run_log` returns the structured per-phase rollup.
- **Per-job source-DB password** (`7f29506`) — `ConnectionConfig.password_inline` and `SourceDbConfig.password_inline` thread the form password through the pipeline, replacing the process-wide env var dependency.
- **PII pointer suppression** (`5421535`) — `pii_priors.is_structural_pointer_name` drops false-positive findings on `_id`/`_uuid`/`_pk`/`_fk`/`_ref` columns. ads.public results: 407 → 208 findings (-49%); API_KEY 174 → 14 (-92%).
- **Empty-table cluster** (`b9fba71`) — zero-record tables are pulled out of Louvain and grouped under one labelled cluster. ads.public: 207 → 28 clusters.
- **Structural-key FK eligibility** (initial commit + follow-ups) — UUID/text-keyed PKs (`STRING_LONG`-class) keep `is_fk_eligible=true` when declared PK / unique / `<x>_id`. Recall on ads.public verifiable set: 10.2 % → 87.5 %.
- **Suffix-id-match candidate gate** — generic `<x>_id → <table>.id` token-overlap rule recovers FKs whose lexical similarity is below the 0.85 bypass threshold.

## Validating end-to-end (smoke checklist)

The fastest way to confirm the stack is healthy after a code change:

```bash
# 1. Start (idempotent)
bash scripts/start.sh

# 2. Health checks
curl -sf http://127.0.0.1:8000/api/health
curl -sf http://127.0.0.1:8080/health   # mock extractor
curl -sf http://localhost:4200/ | grep -o '<title>[^<]*</title>'

# 3. Test-connection probe (good case)
curl -s -X POST http://127.0.0.1:8000/api/test_connection \
  -H 'Content-Type: application/json' \
  -H 'X-Discovery-Token: dev-secret' \
  -d '{"host":"localhost","port":5432,"database":"ads","user":"adsuser","password":"Ads@3421","schema":"public"}'
# → {"ok": true, "server_version": "PostgreSQL 14.2 ...", "table_count": 218, ...}

# 4. Test-connection probe (bad schema → ok:false, error_kind:"schema_missing")
curl -s -X POST http://127.0.0.1:8000/api/test_connection \
  -H 'Content-Type: application/json' \
  -H 'X-Discovery-Token: dev-secret' \
  -d '{"host":"localhost","port":5432,"database":"ads","user":"adsuser","password":"Ads@3421","schema":"nope"}'

# 5. Submit a job (smoke)
JOB=$(curl -s -X POST http://127.0.0.1:8000/api/jobs \
  -H 'Content-Type: application/json' -H 'X-Discovery-Token: dev-secret' \
  -d '{"label":"smoke","host":"localhost","port":5432,"database":"ads","user":"adsuser","password":"Ads@3421","schema":"public"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["job_id"])')

# 6. Watch
curl -s http://127.0.0.1:8000/api/jobs/$JOB | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/jobs/$JOB/run_log | python3 -m json.tool | head -40

# 7. Stop
bash scripts/stop.sh
```

Expected results on `ads.public` (218 tables, 61 with rows):

| metric | value |
|---|---|
| Wall-clock | ~25–35 s |
| Relationships discovered | 95 |
| Recall on verifiable FKs (both sides have data) | 35 / 40 = 87.5 % |
| Precision | 35 / 95 = 36.8 % |
| Clusters | 28 (one of which is `public_empty_tables` with 158 EMPTY tables) |
| PII findings | 208 (down from 407 before pointer suppression) |

## Production deployment

Single-node target: 32-core / 64 GB RAM, 300 GB NVMe SSD. Expected end-to-end runtime ≈ 6–8 h on a 1 TB / ~1 200-table source.

1. Provision Postgres for results: `createdb discovery_results && psql -d discovery_results -f backend/python/pipeline/sql/results_schema.sql`.
2. Install the pipeline: `pip install -e backend/python/pipeline`.
3. Replace the mock with the real extractor (the Spring Boot stub under `backend/java/extraction-service/` mirrors the OpenAPI contract; production swaps it in with vault-resolved `password_secret_ref`).
4. Export env vars (`SOURCE_DB_PASSWORD`, `RESULTS_DB_PASSWORD`, `DISCOVERY_API_TOKEN`, …).
5. Run `bash scripts/start.sh` (or supervise the three processes individually with systemd / runit / kubernetes).
6. Monitor: Prometheus on the extractor's `/actuator/prometheus`; Python pipeline structured logs on stdout (JSON when `LOG_JSON=1`).

## Definition of done

- All POC metrics pass (recall ≥ 0.95 on the synthetic schemas; F1 ≥ 0.92).
- `pytest` green in `backend/python/pipeline/`; `mvn test` green in `backend/java/extraction-service/`.
- `bash scripts/start.sh` ⇒ submit job ⇒ succeed ⇒ stop is reproducible.
- Source-DB password never appears in logs.
- No raw PII reaches the results DB (only redacted samples + counts).
- Java extractor rejects forbidden query patterns (JSqlParser whitelist).
- READMEs and the four core docs (`architecture.md`, `process.md`, `api.md`, `example.md`) are current.

## License

Apache 2.0
