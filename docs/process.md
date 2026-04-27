# Process — one job, end to end

This document traces a single discovery job from "user clicks Run" to
"results visible in the UI". Read alongside [architecture.md](architecture.md)
for the component map, and [example.md](example.md) for a worked example
on a 10-table schema covering nine different relationship patterns
(one-to-many, junction, composite FK, self-ref, polymorphic, JSONB,
UUID-keyed, low-cardinality lookup, PII propagation).

## Timeline of a job

```
                                   T+0      T+1s    T+5s   T+15s   T+25s    T+30s
        UI form submit              │        │       │      │       │        │
            │                       ▼        │       │      │       │        │
            ▼               POST /api/jobs   │       │      │       │        │
   POST /api/jobs ─────► auth check ──► UPSERT jobs ─┘      │       │        │
                          (X-Discovery-Token)        │      │       │        │
                                                     ▼      │       │        │
                                       _reset_pipeline_state│       │        │
                                       (TRUNCATE run_log,   │       │        │
                                        analysis tables)    │       │        │
                                                     │      │       │        │
                                                     ▼      │       │        │
                                       Thread spawns        │       │        │
                                       python -m discovery  │       │        │
                                       run-all              │       │        │
                                                     │      │       │        │
                                                     ▼      │       │        │
                                          inventory ──► extract ──► fingerprint
                                                                        │
                                                                        ▼
                                                                    pii_scan
                                                                        │
                                                                        ▼
                                                          candidate_gen ──► validate
                                                                        │      │
                                                                        ▼      ▼
                                                       composite_fk ── polymorphic_fk
                                                            ─ jsonb_fk ─ inheritance
                                                                        │
                                                                        ▼
                                                       pii_propagation ─► pii_leak
                                                                        │
                                                                        ▼
                                                       clustering ─► report
                                                                        │
                                                                        ▼
                                                       UPSERT jobs (status=succeeded,
                                                              counts populated)
```

Numbers in the timeline are typical for a ~40-table schema. Larger schemas
(public2, hr at ~500 tables) take 2-3 minutes wall.

## Step by step

### Step 0 — User initiates a job

The user opens `http://localhost:4200/submit` and fills a form:

| Field | Default | Validation |
|---|---|---|
| label | (empty) | required |
| schema | (empty) | required |
| host | `localhost` | required |
| port | `5432` | required, ≥1 |
| database | (empty) | required |
| user | (empty) | required |
| password | (empty) | required |

On submit, `JobService.submit(payload)` POSTs to `/api/jobs` with header
`X-Discovery-Token: dev-secret`.

### Step 1 — API accepts the job

`api/main.py:submit_job()` does five things:

1. **Auth gate**. `Depends(_require_secret)` checks `X-Discovery-Token`.
   Fails fast with 401 on missing/wrong token.
2. **Build per-job config**. Creates `tempfile.mkdtemp(prefix=f"disc-{job_id}-")`,
   renders a YAML config under it pointing to the per-job `parquet/` dir,
   the results-DB DSN, and a single-schema scope.
3. **Scrub the password**. `req.model_copy(update={"password": ""})` —
   we never persist the source-DB password. The pipeline reads it from
   the mock extraction service's environment via `password_secret_ref:
   env://SOURCE_DB_PASSWORD`.
4. **Persist initial state**. `_persist_job(job)` UPSERTs into
   `discovery.jobs` immediately so the row exists even if a crash hits
   before the runner starts. UI list renders it as `queued`.
5. **Reset pipeline state**. `_reset_pipeline_state_for_schema()`
   TRUNCATEs `run_log` and every analysis table (relationships, PII
   findings, clusters, etc.) — required because the orchestrator's
   `is_complete(phase, "global", None)` check is global; without this
   the new job would short-circuit on the previous job's `succeeded`
   rows. `discovery.jobs` is intentionally preserved across reset.
6. **Spawn the runner**. A daemon `threading.Thread(target=_run_pipeline)`
   forks `python3 -m discovery run-all --config <yaml>` as a subprocess.
   Stdout/stderr go to `<work_dir>/run.log`.

The HTTP response returns immediately with the new `JobStatus`
(`status=queued` / `running`).

### Step 2 — Pipeline runs 14 phases

The CLI's `run-all` calls `orchestrator.run_all()`, which iterates `ALL_PHASES`
and runs each through `_run_phase()`. Each call:

```
_run_phase(name) →
  if run_log.is_complete(name, "global", None): skip
  else:
    run_log.start(name, "global", None)       # row inserted
    fn(...)                                   # actual phase
    run_log.succeed(name, "global", None)     # row updated
```

Failures roll up: `run_log.fail()` records the exception, the runner
exits non-zero, the API thread sets `job["status"] = "failed"`.

| Phase | Module | What it does |
|---|---|---|
| 1. **inventory** | `inventory.py` | Connects to source DB, lists tables in `schema`, persists rows in `tbl_inventory` + `col_inventory`. Captures declared PK / unique-index metadata. |
| 2. **extract** | `extraction.py` + `extraction_client.py` | For each table: posts a `COPY (SELECT ...)` request to the extraction service. Service streams CSV, writes a single Parquet file under `<work_dir>/parquet/<schema>__<table>.parquet`. Idempotent (skips tables already present + checksum-matching). |
| 3a. **fingerprint** | `fingerprint.py` | One worker per CPU. Each worker reads its assigned column from Parquet, computes a HyperMinHash sketch (1024 buckets × 8 bits) + HLL cardinality + numeric min/max + null %. Adaptive early-stop after 3 row-groups when HLL stabilizes. Pickled sketch goes into `col_inventory.sketch_blob`. |
| 3b. **pii_scan** | `pii_scan.py` | Per column: regex pass + Luhn / stdnum validators + name-prior boost + (default-on) spaCy NER on `STRING_LONG` columns. Multi-hit-per-cell capped at 1. Writes `pii_findings`. Adds `IDENTITY_BUNDLE` table-level rows when ≥2 of {first_name, last_name, email, phone, ssn, address} are present. |
| 4. **candidate_gen** | `candidates.py` | SQL pre-filter (cardinality + type + PK signal + name match) + FAISS LSH search on the sketches → 100s-1000s of candidate (child_col, parent_col) pairs. Post-filters: `dedup_bidirectional_candidates` (keeps the canonical direction via PK > inheritance > row-count-vs-distinct identifier > canonical `id` > alphabetical), `filter_bridge_collisions` (drops ambiguous parents when one wins on name-sim + row-distance, exempting self-refs and declared-PK parents), `apply_range_overlap_penalty` (demotes child<<parent + weak name to advisory tier). Persists to `fk_candidates` with `tier ∈ {primary, advisory_lowconf}`. |
| 5. **validate** | `validate.py` | DuckDB queries each Parquet file. For each `fk_candidates.tier='primary'` pair: `SELECT count of orphans = SELECT count(distinct child) - count(distinct child WHERE EXISTS in parent)`. `containment_full = 1 - orphans/child_distinct`. Survivors with `containment ≥ 0.95` land in `relationships` with the exact `confidence` and `cardinality` (1:1 / 1:N / N:1 / N:M). |
| 4b. **composite_fk** | `composite_fk.py` | Multi-column FK detection — finds (col1, col2) pairs whose tuple values are contained in a parent's (PK1, PK2). Persists to `composite_relationships`. |
| 4c. **polymorphic_fk** | `polymorphic_fk.py` | Detects Rails/Django shape: `entity_type` (low-cardinality string matching parent table names) + `entity_id` (high-cardinality int). Partitions child rows by discriminator value and runs DuckDB containment per partition. Writes `polymorphic_relationships`. |
| 4d. **jsonb_fk** | `jsonb_fk.py` | For columns of type `jsonb`: extracts every leaf path, samples 1000 rows, tests value containment of each path against every PK column. Writes `jsonb_relationships`. |
| **inheritance** | `inheritance.py` | Post-Phase-5 annotator. Tags `relationships.evidence.is_a_inheritance = true` for pairs where both sides are PK + containment = 1.0 + name_sim ≥ 0.95. Catches `vendor.business_entity_id ↔ business_entity.business_entity_id`. |
| **pii_propagation** | `pii_propagation.py` | Reverse-BFS over `relationships` (confidence ≥ 0.8). Roots = tables with a column-level PII finding in the direct-identifier class set (EMAIL, SSN_*, IBAN, CC_NUMBER, IDENTITY_BUNDLE, ...) at score ≥ 0.85. Tags every reachable table on `tbl_inventory.subject_kinds JSONB` with the union of subject types and `subject_link_distance`. No distance decay (under GDPR, depth-5 still counts). |
| **pii_leak** | `pii_leak.py` | Cross-cluster value-set overlap detector. Compares HyperMinHash sketches between every PII direct-identifier column and every NON-PII column from a different table; emits a `pii_leaks` row when containment ≥ 0.5. Reuses sketches already on disk — no re-sampling. |
| **clustering** | `clustering.py` | (1) Junction-collapse: degree-2 PK→PK junction tables become synthetic M:M edges between their two parents. (2) Node typology: tag each table FACT/DIMENSION/LOOKUP/JUNCTION/AUDIT. (3) Weighted Louvain (NetworkX) with `weight = confidence × cardinality_factor + schema_bonus(0.15) + pii_bonus(0.10)`. (4) Auto-name: anchor table → lexical prefix → `cluster_<id>`. Persists `clusters` rows + back-fills `tbl_inventory.cluster_id`. |
| 7. **report** | `report.py` | Emits CSV + Excel artifacts under `<work_dir>/reports/`. |

### Step 3 — API records terminal state

When the subprocess exits:

```python
finally:
    job["ended_at"] = _now()
    stats = _job_stats_from_db(req.schema_name)   # counts from result tables
    job["relationships_count"] = stats["relationships"]
    job["pii_count"]           = stats["pii"]
    job["cluster_count"]       = stats.get("clusters", 0)
    _persist_job(job)                              # final UPSERT
```

The job row is now `status=succeeded` (or `failed` with `error_message`),
all counts populated. UI polling picks it up within 3 seconds.

### Step 4 — UI renders results

Dashboard re-polls `/api/jobs` every 4 seconds. The user navigates to
`/jobs/{job_id}` and lands on the **Clusters tab** (default).

| Click path | What renders |
|---|---|
| Clusters tab → Cards | One card per named cluster, sorted by table_count, color-coded by cluster, PII-marker red border |
| Clusters tab → Cluster graph | vis-network columnar layout — every table is a fixed node, columns by cluster, edges between columns are super-points |
| Click a cluster card | `/jobs/{id}/clusters/{cluster_id}` — table list, intra-cluster edges, PII findings; toggle to ERD view shows tables as cards with cross-cluster bridges colored by their owning cluster |
| Relationships graph tab | Force-directed vis-network, every node tinted by its cluster color, crow's-foot edge endpoints, confidence slider, hierarchical layout option |
| PII findings tab | Sortable table of all `pii_findings` rows |
| Run log tab | Tail of `<work_dir>/run.log` |
| ERD card view link | dbdiagram.io-style schema-wide ERD with column-level connections |
| Export buttons (header) | Downloads DBML / Mermaid / JSON of the discovered graph |

### Step 5 — Persistence + restart safety

- **Every state transition** (queued → running → succeeded/failed, plus
  count updates) writes through to `discovery.jobs`.
- **On restart**, `_load_jobs_from_db()` repopulates the in-memory
  `_jobs` dict with the newest 200 rows. Jobs left as `running` at boot
  are surfaced as `failed` with reason "API restarted while job was in
  flight" — no zombies in the dashboard.
- **All analysis data** (relationships, clusters, PII, ...) lives in the
  results DB independently of the API process. A pipeline run's data
  outlives any number of API restarts.

## Failure modes

| When | Effect | Recovery |
|---|---|---|
| Mock service down | extract phase fails fast on first request | Restart with `start.sh`; the run_log marks `extract` as `failed`, re-running the job clears it |
| Source DB unreachable | inventory phase fails at the very first connect | Same; check `start.sh` env vars |
| Pipeline subprocess crashes | API thread sets `status=failed`, populates `error` from exit code or stderr tail | Inspect `<work_dir>/run.log`; resubmit |
| API restart mid-run | On boot, `_load_jobs_from_db` flips `running` rows to `failed`; the result DB might have partial rows but `_reset_pipeline_state_for_schema` will clear them on next submit | Resubmit |
| Result DB out of disk | `validate` or `clustering` errors mid-write | Free space or rotate parquet under `/tmp/discovery-parquet-*`; resubmit |

## Configuration knobs (high-level)

All in `pipeline/config/default.yaml`, overridable via env-var interpolation
(`${VAR:default}`). Every flag below also accepts a per-job override in the
API-rendered config.

| Setting | Default | Effect |
|---|---|---|
| `pii.scan_rows_per_column` | 50000 | Sample size per column for PII regex |
| `pii.detectors.spacy_ner` | true | Run NER on STRING_LONG columns |
| `relationships.parent_distinct_ratio_min` | 0.95 | Parent must have ≥95% as many distinct values as child to admit |
| `relationships.child_min_distinct_count` | 100 | Cardinality floor (lowered to 2 for role-FK bypass) |
| `relationships.containment_threshold` | 0.95 | Phase-5 promotion threshold |
| `relationships.lsh_threshold` | 0.7 | LSH Jaccard threshold |
| `relationships.validate_only_primary_tier` | true | Phase 5 skips advisory candidates |
| `relationships.semantic_name_similarity` | true | Use sentence-transformers when available |
| `relationships.clustering_enabled` | true | Run Louvain |
| `pii.propagation_enabled` | true | Run subject-rooted reverse-BFS |
| `pii.leak_scan_enabled` | true | Run cross-cluster sketch leak detection |
