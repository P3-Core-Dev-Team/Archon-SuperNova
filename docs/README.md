# Archon-SuperNova

**Undeclared single-column foreign-key discovery and PII detection across large Postgres databases.**

## Architecture

Archon-SuperNova is a two-service system: a Java Spring Boot extraction microservice and a Python coordinator with local workers.

```
┌────────────────────────┐         ┌────────────────────────┐
│ Spring Boot Extraction │  HTTP   │ Python Discovery       │
│ Service (Java 17)      │◄────────│ Pipeline (Python 3.11) │
│                        │         │                        │
│ Multi-source:          │         │ - Coordinator (1)      │
│  - Postgres (COPY)     │         │ - Worker pool          │
│  - MySQL (future)      │         │ - DuckDB analytics     │
│  - Oracle (future)     │         │ - Hyperscan PII        │
│  - SQL Server (future) │         │ - FAISS LSH            │
│                        │         │ - Validation joins     │
│ Owns all source creds  │         │ Owns no source creds   │
└──────────┬─────────────┘         └──────────┬─────────────┘
           │                                  │
           ▼                                  ▼
   Source DBs                         Postgres Results DB
   (Postgres now,                     (queue + findings +
    others later)                      run_log)
                                              │
                          ┌───────────────────┴───────────────┐
                          │ Parquet on local NVMe (~250 GB)   │
                          │ Read by both service (writes) and │
                          │ pipeline (reads)                  │
                          └───────────────────────────────────┘
```

**Why two services:**
- Multi-source support: Java's JDBC ecosystem makes adding MySQL/Oracle/SQL Server straightforward
- Credential isolation: only the service holds source passwords
- Reusable: other tools in your organization can extract via the same API
- Standardized: Spring Boot connection pooling and metrics are mature

**Why single-node production:** A 32-core / 64 GB box with 300 GB NVMe runs the full workload in ~6 hours. DuckDB uses all cores for analytics, Parquet stays local (no network storage), and Spring Boot runs on the same node (separate process) with no distributed coordination overhead.

## Repository Layout

```
discovery/
├── README.md                          # this file
├── PLAN.md                            # build plan & task tracking
├── DECISIONS.md                       # architecture decision log
│
├── openapi/
│   └── extraction-service-v1.yaml     # shared contract (both services)
│
├── extraction-service/                # Java 17 Spring Boot 3.2
│   ├── README.md
│   ├── pom.xml
│   ├── src/main/java/com/discovery/extraction/
│   ├── src/main/resources/
│   └── src/test/java/com/discovery/extraction/
│
├── pipeline/                          # Python 3.11 coordinator + workers
│   ├── README.md
│   ├── pyproject.toml
│   ├── config/default.yaml            # configuration template
│   ├── sql/results_schema.sql         # Postgres schema DDL
│   ├── src/discovery/                 # package source
│   └── tests/                         # pytest unit + integration
│
└── synthetic-data/                    # 30-table test-data generator
    ├── README.md
    ├── pyproject.toml
    ├── src/synthetic_data/
    └── tests/
```

## Quick Start (Development)

### 1. Provision the Postgres instances

You need two Postgres databases — source (the DB to discover) and results (`discovery_results`). Install Postgres 16 locally (or point at an existing instance) and record the DSNs.

```bash
createdb -U postgres discovery_results
psql -U postgres -d discovery_results -f pipeline/sql/results_schema.sql
```

### 2. Install the pipeline

```bash
cd pipeline
pip install -e .[dev]
```

### 3. Start the extraction service

```bash
cd extraction-service
mvn spring-boot:run    # listens on :8080
```

### 4. Initialize the results schema

```bash
discovery init
```

Creates the `discovery` schema with all tables (inventory, candidates, relationships, PII findings, run_log).

### 5. Run the full pipeline

```bash
discovery run-all                     # default: full extract for every table
discovery run-all --two-pass          # 1% sample first, full extract only for FK survivors
discovery run-all --two-pass --sample-pct 0.02   # tune the triage sample (default 0.01 = 1%)
```

Runs phases 1–7 sequentially with automatic resumability. If interrupted, restart the same command to pick up where it left off. The `--two-pass` mode (see "Two-pass model" in `pipeline/README.md`) cuts Phase 2 bytes by ~89% on schemas with many tables that never participate in a foreign-key relationship. Expected runtime: **~6 hours on 32-core / 64 GB hardware**.

Intermediate commands (for testing or manual control):

```bash
discovery inventory                   # Phase 1 only
discovery extract                     # Phase 2 only
discovery fingerprint                 # Phase 3a only
discovery pii-scan                    # Phase 3b only
discovery generate-candidates         # Phase 4 only
discovery validate                    # Phase 5 only
discovery report all                  # Phase 7 only — also writes relationships_advisory.csv
discovery status                      # Show progress from run_log
```

### 6. Reclaim disk space

```bash
discovery cleanup --dry-run           # preview which Parquet files would be GC'd
discovery cleanup                     # delete Parquet for tables not referenced by any surviving FK candidate
discovery cleanup --purge             # nuclear: rmtree the Parquet directory entirely
discovery cleanup --purge --no-keep-results  # also drop the results-DB schema
```

`cleanup` (no flags) is selective: it removes Parquet files for tables whose `tbl_inventory.status='extracted'` but appear on neither side of any surviving `fk_candidates` row. Disk-cap enforcement (`storage.parquet_cap_bytes`, default 250 GB) runs the same GC after each extract pass.

All commands support `--config <path>`, `--dry-run`, and `--limit N` (for sampling).

## Production Deployment

From **DISCOVERY_PIPELINE_V2.md § 11 Stage C**:

1. **Provision hardware:** 32-core / 64 GB RAM node, 300 GB NVMe SSD
2. **Deploy results Postgres:** Separate instance, `discovery` schema pre-created
3. **Deploy extraction service:** `mvn -q package` → `java -jar target/extraction-service-*.jar` on same node as Python
4. **Deploy Python pipeline:** `python -m venv .venv && pip install -e pipeline/`
5. **Run initialization:** `discovery init` to verify connectivity
6. **Review excluded tables:** `discovery inventory` and inspect `run_log` for any import warnings
7. **Launch overnight:** `discovery run-all` (expect completion in 6–8 hours)
8. **Generate reports:** `discovery report all` to export findings as CSV/Excel

Monitor via Prometheus metrics on extraction service (`:8080/actuator/prometheus`) and Python worker exports (`:9009/metrics`).

## Timing Expectations

Realistic breakdown on 32-core / 64 GB node with 1 TB / ~1200-table Postgres source:

| Phase | Time | Notes |
|---|---|---|
| 1 — Inventory | 5–10 min | Service-mediated DDL queries |
| 2 — Extraction | 2–4 hrs | Spring Boot service bottleneck; 8 parallel extracts |
| 3a — Fingerprinting | 30–60 min | 16-way worker pool, xxh3 + HyperMinHash |
| 3b — PII Scan | 20–45 min | Hyperscan SIMD multi-pattern matching |
| 4 — Candidate Gen | 5–20 min | SQL pre-filter + FAISS LSH |
| 5 — Validation | 1–2 hrs | DuckDB local joins on Parquet |
| 7 — Reporting | 5–10 min | CSV/Excel exports |
| **Total** | **~6 hours** | Plan for overnight run; monitor via run_log |

Variance driven by: Phase 2 throughput (Spring Boot service), column count per table (Phase 3a/3b), FK candidate count (Phase 5 joins).

## Definition of Done

From **DISCOVERY_PIPELINE_V2.md § 12**:

- All POC metrics pass (Recall ≥ 0.95, F1 ≥ 0.92)
- All tests pass (`pytest -q` and `mvn test`)
- `discovery run-all` against synthetic source produces correct results
- Killing and restarting produces identical final state (resumability verified)
- Source DB password never appears in logs
- No raw PII in results DB
- Spring Boot service rejects forbidden query patterns
- Production run completes within 8 hours on target hardware
- READMEs cover: install, configure, init, run, monitor, troubleshoot
- DECISIONS.md documents every threshold tuning and library choice

## Documents

- **DISCOVERY_PIPELINE_V2.md** (or `docs/plan-v2.md`): Authoritative specification — complete goals, constraints, architecture, tech stack, phases, timing, build order, and definition of done.
- **discovery_system_prompt_addendum_2_springboot.md** (or `docs/spring-boot-addendum.md`): Spring Boot service detailed spec — multi-source extractors, OpenAPI contract, testing, configuration, security.
- **PLAN.md** (this repo): Your living build checklist with 32 items organized by stage (Setup / OpenAPI / Spring Boot / Python / Integration).
- **DECISIONS.md** (this repo): Decision log with 6 locked ADRs and pending questions for future design choices.

## Contributing

1. Open a pull request with your changes.
2. Run the test suites: `mvn -q verify` in `extraction-service/`, `pytest` in `pipeline/`.
3. Ensure all tests pass and DECISIONS.md is updated if you change thresholds, library choices, or architecture.

## License

Apache 2.0
