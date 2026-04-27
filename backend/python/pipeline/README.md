# Discovery Pipeline

**Multi-phase foreign-key and PII discovery on local Parquet data via DuckDB.**

A Python 3.11 coordinator with multiprocessing workers that orchestrates discovery phases. All source data is extracted via the Spring Boot extraction service and analyzed locally on Parquet without ever opening a source database connection.

## Purpose

1. **Inventory** — catalog all tables and columns from the source database
2. **Extract** — fetch all table data from source via extraction service, store as Parquet
3. **Fingerprint** — compute HyperMinHash sketches for containment estimation
4. **PII Scan** — detect personally identifiable information using Hyperscan + secondary validators
5. **Candidate Generation** — use FAISS LSH to propose foreign-key relationships
6. **Validate** — confirm candidates on full local data via DuckDB joins
7. **Report** — export findings as CSV/Excel

## Install

```bash
cd pipeline
pip install -e .[dev]
```

For production (no dev dependencies):

```bash
pip install -e .
```

Requires Python 3.11+.

## Configuration

Primary configuration: `config/default.yaml` (in repo) and environment variable overrides.

Example:

```yaml
extraction_service:
  base_url: http://localhost:8080
  auth_token: ${EXTRACTION_SERVICE_TOKEN}
  request_timeout_seconds: 3600

source_db:
  type: postgres
  host: ${SOURCE_DB_HOST}
  port: ${SOURCE_DB_PORT:5432}
  database: ${SOURCE_DB_NAME}
  user: ${SOURCE_DB_USER}
  password_secret_ref: env://SOURCE_DB_PASSWORD

results_db:
  host: ${RESULTS_DB_HOST}
  port: ${RESULTS_DB_PORT:5432}
  database: discovery_results
  user: ${RESULTS_DB_USER}
  password: ${RESULTS_DB_PASSWORD}

storage:
  base_path: /data/parquet
  duckdb_temp_dir: /data/duckdb_tmp
  duckdb_memory_limit: "32GB"
  parquet_cap_bytes: 268435456000        # 250 GB; cleanup GC fires when the
                                         # extracted Parquet footprint crosses this

orchestration:
  workers:
    extract: 8
    fingerprint: 16
    pii_scan: 16
    validate: 8                          # YAML key 'validate'; Python attr 'validate_workers'

fingerprint:
  early_stop_delta: 0.005                # adaptive HLL early-stop: stop reading row groups
                                         # once two consecutive deltas drop below this

pii:
  match_rate_threshold: 0.05             # minimum regex match rate to keep a finding
                                         # (column-name priors override this floor)
  detectors:
    spacy_ner: false                     # opt-in; requires spacy + en_core_web_sm

extraction:
  column_projection: true                # SELECT only fk_eligible / pii-eligible columns

relationships:
  require_parent_pk: true                # parent must have is_pk OR is_unique_indexed
  validate_only_primary_tier: true       # Phase 5 skips fk_candidates.tier='advisory_lowconf'
```

All environment variables are interpolated at runtime. Use `${VAR}` for required vars, `${VAR:default}` for optional.

### New tuning knobs

| Key | Default | Effect |
|---|---|---|
| `relationships.require_parent_pk` | `true` | Drops candidates where the parent column has neither `is_pk` nor `is_unique_indexed`; the dominant precision lever (see ADR-012). |
| `relationships.validate_only_primary_tier` | `true` | Phase 5 reads only `fk_candidates.tier='primary'`. `advisory_lowconf` rows are emitted to `relationships_advisory.csv` audit-only. |
| `extraction.column_projection` | `true` | Phase 2 pulls only fk-eligible / pii-eligible columns from each table. Falls back to `SELECT *` when no inventory metadata is available yet. |
| `fingerprint.early_stop_delta` | `0.005` | Adaptive HLL early-stop: when two consecutive row-group deltas drop below this fraction (after at least 3 row groups), stop reading. |
| `pii.detectors.spacy_ner` | `false` | Opt-in NER pass on STRING_LONG columns. Requires `pip install spacy` plus `python -m spacy download en_core_web_sm` (~13 MB). |
| `pii.match_rate_threshold` | `0.05` | Minimum raw regex match rate. A column-name prior (e.g. column literally named `ssn`) overrides this floor for the matching pattern. |
| `storage.parquet_cap_bytes` | `268435456000` (250 GB) | Soft cap; `cleanup.enforce_disk_cap` runs orphan GC and warns when the total Parquet footprint exceeds the cap. |

## CLI

All commands support `--config <path>` and `--dry-run`.

### Lifecycle Commands

```bash
discovery init                        # Create results schema, verify connectivity

discovery inventory                   # Phase 1 only
discovery extract [--limit N]         # Phase 2 only
discovery fingerprint                 # Phase 3a only
discovery pii-scan                    # Phase 3b only
discovery generate-candidates         # Phase 4 only
discovery validate [--limit N]        # Phase 5 only
discovery report <format>             # Phase 7 only (csv, excel, html)
discovery report all                  # also writes relationships_advisory.csv

discovery run-all                     # Phases 1–7, fully resumable
discovery run-all --two-pass          # 1% sample first, full extract for FK survivors only
discovery run-all --two-pass --sample-pct 0.02   # tune triage sample (default 0.01)
discovery status                      # Progress from run_log

discovery cleanup --dry-run           # preview the GC plan, write nothing
discovery cleanup                     # selective GC: drop Parquet for tables not in any
                                      # surviving fk_candidates row
discovery cleanup --purge             # nuclear: rmtree the Parquet directory
discovery cleanup --purge --no-keep-results   # also drop results-DB schema (with --purge only)
```

### Options

- `--config <path>` — override default config file
- `--dry-run` — log what would happen without making changes
- `--limit N` — for extract/validate, process only first N tables/candidates
- `--two-pass` (run-all) — enable two-pass extraction (see "Two-pass model" below)
- `--sample-pct FLOAT` (run-all) — sample fraction for the two-pass triage extract; default `0.01` (1%)
- `--purge` (cleanup) — rmtree the Parquet directory instead of selective GC
- `--keep-results / --no-keep-results` (cleanup) — toggle results-DB schema preservation. Default keeps it; pass `--no-keep-results` together with `--purge` to drop the schema as well.
- `-v, --verbose` — enable debug logging (structlog JSON output)

## Two-pass model

`discovery run-all --two-pass` exchanges one full table scan for two cheaper ones in series:

1. **Phase 1** — inventory as usual.
2. **Phase 2a (sample)** — `mode='sample'` with `TABLESAMPLE BERNOULLI(sample_pct)`. Default `--sample-pct 0.01` reads ~1% of every table. Output Parquet is written to the regular path (no `.sample.` suffix).
3. **Phase 3a / 3b / 4** — fingerprint, PII scan, and candidate generation run on the sampled Parquet. The cheap sketches and tier classifier on this small footprint are enough to identify which tables can never participate in a foreign key.
4. **Phase 2b (full subset)** — re-extract only the tables that survive as either child or parent in `fk_candidates` (`mode='full_subset'`, `table_ids=...`). Same Parquet path; the sample is overwritten in place.
5. **Phase 3a re-fingerprint** — `col_inventory.fingerprinted_at` is cleared for the affected columns so Phase 3a's resume guard re-runs them on full data.
6. **Phase 5 / 7** — validate on full data for surviving pairs; report.

**Expected speedup:** ~89% reduction in Phase 2 bytes on schemas with the typical long tail of audit / log / archive tables that never participate in foreign-key relationships. The 1% triage cost is small because the same Parquet path is reused, so no wasted I/O lingers on disk.

**Caveats:**
- Phase 3b PII findings on a 1% sample can miss long-tail PII in `STRING_LONG` columns. For PII-critical workloads, consider a follow-on full PII scan once the survivors are extracted (or skip `--two-pass` entirely).
- Cardinality estimates from sampled fingerprints are noisier; FK *recall* is preserved by the tier system (advisory candidates are still emitted) but raw containment for borderline candidates may shift between the sample and full pass.

## Phases

### Phase 1 — Inventory (Coordinator)

Queries source database for all tables and columns via extraction service. Results:
- `tbl_inventory` — one row per table with schema, row count estimate, size, status, exclusion reason
- `col_inventory` — one row per column with name, type, nullability, cardinality estimate, sketch_blob

Output: `col_inventory` populated with cardinality estimates via DuckDB on extracted metadata.

### Phase 2 — Extraction (Workers)

Each worker extracts one table at a time via extraction service. Results stored as Parquet files named `{schema}__{table}.parquet`.

- Workers: 8 (source DB throttled at 8 concurrent calls)
- Resumability: `run_log` tracks (extract, table, table_id) → skip already-extracted
- Output: Parquet files on `/data/parquet`, manifest entries in `tbl_inventory.parquet_path` and `tbl_inventory.parquet_bytes`

### Phase 3a — Fingerprinting (Workers)

Compute HyperMinHash sketches (cardinality + containment signatures) for all columns.

- Workers: 16 (CPU-bound, xxh3 hashing + HyperMinHash)
- Resumability: skip columns where `col_inventory.fingerprinted_at IS NOT NULL`. (`run_log` rows are also written for telemetry but are NOT consulted for resume — see codeflow audit.)
- Output: `col_inventory.sketch_blob` (BYTEA, ~128 bytes per column), `col_inventory.cardinality_estimate`, `col_inventory.cardinality_method`

### Phase 3b — PII Scan (Workers)

Scan first 50K rows of each column for PII using Hyperscan (primary) plus
secondary validators.

- Workers: 16 (CPU-bound, regex pattern matching)
- Pattern catalog: **47 patterns** in `discovery.pii_patterns.PATTERNS`
  (up from the original 8). Categories:
  - **Generic identity**: EMAIL, PHONE_US, PHONE_E164, SSN_US, DOB
  - **Passports**: PASSPORT_US, PASSPORT_GB, PASSPORT_IN
  - **National IDs**: AADHAAR_IN, NHS_GB, NINO_GB, NRIC_SG, CPF_BR,
    CURP_MX, BSN_NL, PESEL_PL, DNI_ES, NIR_FR, TAX_ID_DE,
    CODICE_FISCALE_IT, PAN_IN, ITIN_US, MEDICARE_MBI_US, NPI_US,
    PERSONNUMMER_SE, DL_US, VAT_EU
  - **Financial**: CC_NUMBER, IBAN, SWIFT_BIC, ABA_ROUTING_US
  - **Network**: IPV4, IPV6, MAC_ADDR
  - **Secrets / credentials**: AWS_ACCESS_KEY_ID, AWS_SECRET, GCP_API_KEY,
    JWT, GH_PAT, PRIVATE_KEY_PEM, API_KEY
  - **Crypto**: BTC_ADDR, ETH_ADDR
  - **Health**: MRN, ICD10
  - **Geography**: GEO_COORD, POSTAL_CODE
- Span-overlap resolution (`pii_score._resolve_overlaps`): when two patterns
  match overlapping byte spans on the same value, the more specific category
  wins. Priority rule: **CC > PHONE > generic**, otherwise the higher
  `SPECIFICITY` score wins. Prevents a 16-digit credit-card from also
  registering as a PHONE_E164 hit on the same span.
- Column-name priors (`pii_priors`): a column literally named `ssn`,
  `email_address`, `passport_no`, etc. raises a `name_prior=true` flag for
  the matching pattern even when the regex match rate falls below
  `pii.match_rate_threshold` (default 0.05). Lets a 5-row sample with poorly
  formatted SSNs still be flagged when the column name is unambiguous.
- Optional spaCy NER (`pii.detectors.spacy_ner`, **off by default**): when
  enabled, `STRING_LONG` columns are augmented with spaCy NER entities
  (PERSON / GPE / ORG / LOC / DATE) on top of regex findings. Requires
  `pip install spacy` and `python -m spacy download en_core_web_sm`.
- Bayesian confidence score (`pii_score.bayesian_score`):
  `score = 1 − (1−π_name)·(1−π_match)·(1−π_validate)` where π_name is the
  column-name-prior weight, π_match is the regex match rate, and π_validate
  is the share of matches that passed checksum validation (Luhn, stdnum
  IBAN/CPF/etc., libphonenumber). Persisted as `pii_findings.score`.
- Validators (verified against `discovery.pii_locale`):
  - python-stdnum: IBAN, US-SSN, UK-NHS, VAT, CPF, CURP, BSN, PESEL,
    Codice Fiscale, NRIC, NIR, NIE/DNI, Personnummer.
  - python-luhn: credit-card check.
  - phonenumbers (Google libphonenumbers): PHONE_US / PHONE_E164.
  - detect-secrets: high-entropy credential gate.
- Resumability: skip columns that already have a `pii_findings` row plus
  consult `run_log` for previously-succeeded columns (`pii_scan` re-uses
  the `run_log` resume-filter pattern from extraction).
- Output: `pii_findings` table with match counts, raw vs post-validator
  match rates, the Bayesian confidence score, the `name_prior` flag, the
  pattern's `specificity` weight, redacted samples, and detector names.

### Phase 4 — Candidate Generation (Coordinator)

**4a. SQL Pre-filter:**
Self-join on indexed `col_inventory` columns: find all (child, parent) pairs where:
- `child.type_class == parent.type_class`
- `child.distinct_count > parent.distinct_count` (child ⊆ parent cardinality)
- `child.is_fk_eligible = true`

Output: `fk_candidates` with source_stage = 'sql_prefilter'.

**4b. FAISS LSH:**
Load all HyperMinHash sketches into FAISS binary index, query each child column as a candidate set of parents. Rank by Hamming distance (containment estimate).

- Filters to: candidates where estimated containment ≥ `lsh_threshold` (default 0.7)
- Output: `fk_candidates` with source_stage = 'lsh_search', estimated_containment

**4c. Precision gates and tier classification (see ADR-012):**
Every emitted candidate is gated and tagged with a `tier`:

- **Parent gate** (`relationships.require_parent_pk`, default `true`): the parent column must be `is_pk` OR `is_unique_indexed`. Tables that have no PK / unique-indexed column at all are exempt (we don't want to suppress all FK signal on legacy tables); on tables that *do* have such metadata, all non-PK / non-unique parents are dropped.
- **Both-PK rule**: when child and parent are both `is_pk`, require `name_similarity > 0.7` (`_PRIMARY_NAME_SIM`); otherwise demote to `advisory_lowconf`.
- **Dense-serial rule**: when both child and parent look like dense integer serials (auto-increment surrogate keys), require `name_similarity > 0.6`; otherwise demote.
- **Confidence formula** (`scoring.compute_confidence`):
  `confidence = 0.40·containment_full + 0.30·name_similarity + 0.15·parent_pk_bonus + 0.10·card_ratio_score + 0.05·sketch_jaccard`
  with `parent_pk_bonus ∈ {1.0 (is_pk), 0.5 (is_unique_indexed), 0.0}`.

Tier values written to `fk_candidates.tier`:

- `primary` — passes Phase 5 validation.
- `advisory_lowconf` — audit-only; Phase 5 skips it. Surfaced via `discovery report all` → `relationships_advisory.csv`.

Effect on a representative HR run: ~131K raw candidates collapsed to a few hundred `primary` candidates without losing FK recall (the dropped pairs were dominated by `id ↔ id` cross-table noise, which the both-PK / dense-serial gates eliminate).

### Phase 5 — Validation (Workers)

For each `primary`-tier FK candidate, run a DuckDB hash anti-join on full local Parquet:

```sql
WITH c AS (SELECT child_col FROM child_table),
     p AS (SELECT parent_col FROM parent_table)
SELECT COUNT(*) AS matches
FROM c LEFT JOIN p ON c.child_col = p.parent_col
WHERE p.parent_col IS NULL;
```

If `matches == 0` or `matches / COUNT(child_table) <= containment_threshold`, relationship is valid.

- Workers: 8 (DuckDB uses 4 cores per query; total ~32 cores utilized; `validate_workers` in YAML is `validate:`).
- Tier filter: only `fk_candidates.tier='primary'` rows are validated; `advisory_lowconf` is audit-only (see ADR-012).
- Anti-join formulation: the explicit `LEFT JOIN ... IS NULL` lets DuckDB plan a hash anti-join in one pass over the child + parent.
- Parent-set materialisation: when multiple candidates share a parent column, parent distinct values are materialised once into a `parent_set` temp table per group (created and dropped within the `with-finally` block).
- Resumability: pulls candidates by anti-joining `fk_candidates` against `run_log` (no Python-side filter loop) → restartable mid-batch.
- Output: `relationships` table with `containment_full`, `cardinality` (ONE_TO_ONE / MANY_TO_ONE / PARTIAL), `confidence`, `evidence` (JSON).

### Phase 7 — Reporting (Coordinator)

Export findings to CSV, Excel, or HTML. Queries:

```sql
SELECT t_child.schema_name, t_child.table_name, c_child.column_name,
       t_parent.schema_name, t_parent.table_name, c_parent.column_name,
       r.containment_full, r.cardinality, r.confidence
FROM relationships r
  JOIN col_inventory c_child ON r.child_col_id = c_child.column_id
  JOIN tbl_inventory t_child ON c_child.table_id = t_child.table_id
  JOIN col_inventory c_parent ON r.parent_col_id = c_parent.column_id
  JOIN tbl_inventory t_parent ON c_parent.table_id = t_parent.table_id
ORDER BY r.confidence DESC;
```

Output: `/data/reports/relationships.{csv,xlsx,html}` and `/data/reports/pii_findings.{csv,xlsx,html}`. `discovery report all` additionally writes `/data/reports/relationships_advisory.csv` containing the `fk_candidates.tier='advisory_lowconf'` rows that Phase 5 did not validate (audit / governance review only — these are NOT promoted to `relationships`).

## Running Tests

### Unit Tests

```bash
pytest tests/unit/
```

Tests for config parsing, extraction client mocks, fingerprinter logic, PII detectors, FAISS integration.

### Integration Tests

```bash
pytest tests/integration/
```

Spins up ephemeral Postgres via Testcontainers, runs full pipeline against 30-table synthetic fixture.

Expected: all phases complete in < 10 minutes on dev machine, all metrics match fixture ground truth.

### All Tests

```bash
pytest                              # unit + integration
pytest -m "not integration"         # unit only
pytest -m "not slow"                # exclude long-running tests
pytest -v                           # verbose output
pytest -k "test_faiss"              # filter by test name
```

## Observability

### Logging

Structured JSON logs via `structlog` to stdout (or file if `DISCOVERY_LOG_FILE` set):

```json
{
  "timestamp": "2026-04-24T12:34:56.789Z",
  "level": "INFO",
  "phase": "fingerprint",
  "table": "users",
  "worker_id": 3,
  "columns_processed": 42,
  "duration_seconds": 12.3,
  "event": "phase_complete"
}
```

Enable debug logging with `-v` or `DISCOVERY_LOG_LEVEL=DEBUG`.

### Metrics

Prometheus metrics exported on `:9009/metrics` (override the bind port via
`observability.metrics_port` in YAML).  The metric names that actually fire
in code (per `discovery.metrics`):

```
discovery_tasks_total{phase, status}              # status: 'succeeded' | 'failed'
discovery_task_duration_seconds{phase}            # histogram, buckets up to 2h
discovery_rows_processed_total{phase}             # extract / fingerprint / pii_scan / validate
discovery_bytes_processed_total{phase}
discovery_parquet_bytes_on_disk                   # gauge
discovery_tables_pending                          # gauge, sourced from tbl_inventory.status
discovery_tables_done                             # gauge, extracted + analyzed
```

Status labels follow `run_log.status` (`succeeded` / `failed`).  Note that
`orchestrator.py` currently emits the legacy values `success` / `failure`;
that's a known mismatch and will be unified in a follow-up pass — until
then dashboards must accept both vocabularies for the `tasks_total` metric.

Scrape with:

```bash
curl http://localhost:9009/metrics
```

## Resumability

Every phase is idempotent. The `run_log` table (in results DB) tracks:

```
(phase, scope_type, scope_id) → status (started | succeeded | failed | skipped)
```

If you kill the pipeline mid-Phase 2 (extraction), restart `discovery extract` and it will:
1. Query `run_log` for all (extract, table, *) rows with status='succeeded'
2. Skip those tables
3. Resume with the next unfinished table

Final state is identical: same rows in `relationships`, `pii_findings`, `run_log`.

**Important:** Resumability depends on idempotent Parquet writes and Postgres writes. If a partial table Parquet exists, delete it manually before resuming:

```bash
rm /data/parquet/public__partially_extracted_table.parquet
```

## Development Workflow

1. **Modify source:** edit `src/discovery/phases/*.py` or `workers/*.py`
2. **Run tests locally:** `pytest tests/unit/`
3. **Test against dev stack:** start the extraction service and a pair of local Postgres instances, then `discovery run-all --limit 5`
4. **Commit:** push branch, CI runs full test suite via GitHub Actions
5. **Review:** PR review checks config generation, dependency versions, DECISIONS.md updates

## Building for Production

```bash
python -m build
pip install dist/discovery-*.whl
```

Ship the wheel to the target node and install into a venv alongside the extraction service JAR.

## Related Documentation

- **DISCOVERY_PIPELINE_V2.md § 6–9** — Phases, CLI, configuration, timing
- **discovery_system_prompt_addendum_2_springboot.md § "Python Pipeline — Required Changes"** — extraction client integration
- **PLAN.md § B24–B32** — Build checklist for this service
- **extraction-service/README.md** — Extraction service API and configuration

## Troubleshooting

### "Connection to results DB refused"

Check that Postgres is running and accessible:

```bash
discovery init --dry-run
```

### "Extraction service unavailable"

Check that Spring Boot service is running:

```bash
curl -H "Authorization: Bearer $(echo $EXTRACTION_SERVICE_TOKEN)" \
  http://localhost:8080/actuator/health
```

### Phase 2 slow (< 100 MB/min from extraction service)

See **extraction-service/README.md § Troubleshooting**.

### Phase 3b (PII) takes > 1 hour

Hyperscan installation or fallback to Python regex. Check logs:

```bash
discovery pii-scan --verbose 2>&1 | grep -i hyperscan
```

If regex fallback is active, PII scan is slower but still works.

### Phase 5 (validation) takes > 4 hours

Too many `primary`-tier FK candidates. Confirm the precision gates are doing their job:

```sql
SELECT tier, COUNT(*) FROM discovery.fk_candidates GROUP BY tier;
-- Healthy ratio: thousands of advisory_lowconf, hundreds-to-low-thousands primary.
```

If `primary` is in the tens of thousands, raise `lsh_threshold` or
`relationships.containment_threshold`. Make sure
`relationships.require_parent_pk: true` is set (default) — disabling it lets
non-PK parents through and drives candidate count up by an order of magnitude.

Expected: 100–5000 `primary` candidates for a typical schema.

### Disk full during Phase 2 / "parquet_cap_bytes exceeded" warning

After Phase 4 has identified surviving FK pairs, `discovery cleanup` removes
the orphaned Parquet files for tables that don't appear in any candidate:

```bash
discovery cleanup --dry-run     # see what would be deleted
discovery cleanup               # selective GC
```

The same GC fires automatically after each extract pass when total bytes
under `storage.base_path` exceed `storage.parquet_cap_bytes` (default 250 GB).
The cap is advisory — the run does not abort, but a warning is logged and
GC is invoked.
