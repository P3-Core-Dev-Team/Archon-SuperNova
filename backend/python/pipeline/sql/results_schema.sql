BEGIN;

CREATE SCHEMA IF NOT EXISTS discovery;
SET search_path TO discovery;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS tbl_inventory (
    table_id          BIGSERIAL PRIMARY KEY,
    schema_name       TEXT NOT NULL,
    table_name        TEXT NOT NULL,
    row_count_estimate BIGINT,
    byte_size_estimate BIGINT,
    status            TEXT NOT NULL DEFAULT 'pending',
    exclusion_reason  TEXT,
    parquet_path      TEXT,
    parquet_bytes     BIGINT,
    extracted_at      TIMESTAMPTZ,
    -- Subject-kind tagging from the PII propagation phase: a JSONB array of
    -- direct-identifier classes that reach this table via FK closure
    -- (e.g. ["EMAIL","SSN_US"]).  NULL until ``propagate-pii`` has run.
    subject_kinds     JSONB,
    -- Closure depth from the nearest direct-identifier root at the time the
    -- propagation tagged this table (0 = direct hit, >0 = FK-reachable).
    subject_link_distance INTEGER,
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (schema_name, table_name),
    CHECK (status IN ('pending','excluded','extracted','analyzed'))
);

-- Idempotent additive migration for older databases where tbl_inventory
-- pre-dates the subject-kind columns.  See discovery.pii_propagation.
ALTER TABLE tbl_inventory ADD COLUMN IF NOT EXISTS subject_kinds JSONB;
ALTER TABLE tbl_inventory ADD COLUMN IF NOT EXISTS subject_link_distance INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'tbl_inventory_updated_at'
    ) THEN
        CREATE TRIGGER tbl_inventory_updated_at
            BEFORE UPDATE ON tbl_inventory
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at();
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS col_inventory (
    column_id         BIGSERIAL PRIMARY KEY,
    table_id          BIGINT NOT NULL REFERENCES tbl_inventory,
    column_name       TEXT NOT NULL,
    ordinal_position  INT NOT NULL,
    data_type         TEXT NOT NULL,
    type_class        TEXT NOT NULL,
    is_nullable       BOOLEAN NOT NULL,
    is_pk             BOOLEAN NOT NULL DEFAULT false,
    is_unique_indexed BOOLEAN NOT NULL DEFAULT false,
    is_indexed        BOOLEAN NOT NULL DEFAULT false,
    is_fk_eligible    BOOLEAN NOT NULL DEFAULT true,
    max_length        INT,
    distinct_count    BIGINT,
    null_pct          REAL,
    min_val           TEXT,
    max_val           TEXT,
    cardinality_estimate BIGINT,
    cardinality_method TEXT,
    sketcher_kind     TEXT NOT NULL DEFAULT 'hyperminhash',
    sketch_blob       BYTEA,
    fingerprinted_at  TIMESTAMPTZ,
    -- physical_type: parquet physical type family populated post-extraction
    -- (extraction.py reads parquet schema via pyarrow and writes here).
    -- Canonical UPPER-CASE family: INTEGER, BIGINT, VARCHAR, BOOLEAN, DATE,
    -- TIMESTAMP, DOUBLE, REAL, BLOB.  NULL until the table is extracted.
    physical_type     TEXT,
    UNIQUE (table_id, column_name)
);

-- Idempotent additive migration for existing deployments where col_inventory
-- pre-dates the physical_type column.  Postgres 9.6+ supports IF NOT EXISTS.
ALTER TABLE col_inventory ADD COLUMN IF NOT EXISTS physical_type TEXT;

CREATE INDEX IF NOT EXISTS idx_col_inventory_fk_search
    ON col_inventory (type_class, distinct_count, is_fk_eligible)
    WHERE is_fk_eligible;

CREATE INDEX IF NOT EXISTS idx_col_inventory_table_id
    ON col_inventory (table_id);

CREATE TABLE IF NOT EXISTS fk_candidates (
    candidate_id      BIGSERIAL PRIMARY KEY,
    child_col_id      BIGINT NOT NULL REFERENCES col_inventory,
    parent_col_id     BIGINT NOT NULL REFERENCES col_inventory,
    estimated_containment REAL,
    name_similarity   REAL,
    type_match        BOOLEAN NOT NULL,
    source_stage      TEXT NOT NULL,
    joint_estimate    BIGINT,
    -- tier: 'primary' (validated by Phase 5) or 'advisory_lowconf'
    -- (audit only, Phase 5 skips). Added by the FK-precision pass.
    tier              VARCHAR(32) NOT NULL DEFAULT 'primary',
    created_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (child_col_id, parent_col_id)
);

-- Idempotent additive migration for older databases where fk_candidates
-- pre-dates the tier column.
ALTER TABLE fk_candidates
    ADD COLUMN IF NOT EXISTS tier VARCHAR(32) NOT NULL DEFAULT 'primary';

CREATE INDEX IF NOT EXISTS idx_fk_candidates_child
    ON fk_candidates (child_col_id);

CREATE INDEX IF NOT EXISTS idx_fk_candidates_parent
    ON fk_candidates (parent_col_id);

CREATE INDEX IF NOT EXISTS idx_fk_candidates_tier
    ON fk_candidates (tier);

CREATE TABLE IF NOT EXISTS relationships (
    rel_id            BIGSERIAL PRIMARY KEY,
    child_col_id      BIGINT NOT NULL REFERENCES col_inventory,
    parent_col_id     BIGINT NOT NULL REFERENCES col_inventory,
    containment_full  REAL,
    cardinality       TEXT NOT NULL,
    confidence        REAL,
    evidence          JSONB,
    validated_locally BOOLEAN NOT NULL DEFAULT true,
    validation_method TEXT NOT NULL DEFAULT 'local_duckdb_full',
    discovered_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (child_col_id, parent_col_id)
);

CREATE INDEX IF NOT EXISTS idx_relationships_child
    ON relationships (child_col_id);

CREATE INDEX IF NOT EXISTS idx_relationships_parent
    ON relationships (parent_col_id);

CREATE TABLE IF NOT EXISTS pii_findings (
    finding_id        BIGSERIAL PRIMARY KEY,
    -- column_id is nullable to allow table-level findings such as the
    -- IDENTITY_BUNDLE synthesis emitted when ≥2 direct identifiers cluster
    -- on a single table (table_id is supplied instead).  See pii_scan.
    column_id         BIGINT REFERENCES col_inventory,
    -- table_id is populated for table-level findings (column_id IS NULL).
    -- For column-level findings it is left NULL — the column_id alone
    -- already identifies the owning table.
    table_id          BIGINT REFERENCES tbl_inventory,
    pii_type          TEXT NOT NULL,
    detector          TEXT NOT NULL,
    match_count       INT NOT NULL,
    sample_count      INT NOT NULL,
    match_rate        REAL NOT NULL,
    -- regex_match_rate: raw pre-validator match rate (before stdnum/Luhn).
    -- Lets the report show why a high-match candidate was filtered.  Added by C5.
    regex_match_rate  REAL,
    -- name_prior: true if the column NAME hinted at this PII type
    -- (e.g. column 'ssn' → boost SSN_US).  Added by C5.
    name_prior        BOOLEAN DEFAULT false,
    -- score: post-validator confidence in [0, 1].  Added by C5.
    score             REAL,
    -- specificity: pattern-specificity tier (smaller = more specific, less
    -- ambiguous).  Added by C5.
    specificity       INTEGER,
    validated         BOOLEAN NOT NULL DEFAULT false,
    redacted_examples JSONB,
    detected_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (column_id, pii_type, detector)
);

-- Idempotent additive migrations for older databases that pre-date the
-- table-level finding support (column_id NULL + table_id NOT NULL pair).
ALTER TABLE pii_findings ALTER COLUMN column_id DROP NOT NULL;
ALTER TABLE pii_findings ADD COLUMN IF NOT EXISTS table_id BIGINT
    REFERENCES tbl_inventory;
-- Partial unique index for table-level findings (column_id IS NULL).  The
-- regular UNIQUE (column_id, pii_type, detector) constraint deduplicates
-- column-level rows; this partial index does the same for table-level rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pii_findings_table_level
    ON pii_findings (table_id, pii_type, detector)
    WHERE column_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_pii_findings_col
    ON pii_findings (column_id);

-- Idempotent additive migration for older databases where pii_findings
-- pre-dates the C5 confidence-scoring columns.  Postgres 9.6+ supports
-- ADD COLUMN IF NOT EXISTS; running these against a fresh install (where
-- the columns already exist via the CREATE TABLE above) is a no-op.
ALTER TABLE pii_findings
    ADD COLUMN IF NOT EXISTS regex_match_rate REAL;
ALTER TABLE pii_findings
    ADD COLUMN IF NOT EXISTS name_prior BOOLEAN DEFAULT false;
ALTER TABLE pii_findings
    ADD COLUMN IF NOT EXISTS score REAL;
ALTER TABLE pii_findings
    ADD COLUMN IF NOT EXISTS specificity INTEGER;
-- IIN/BIN provider breakdown — JSONB array of {brand, count, share}
-- entries, populated only for CC_NUMBER findings.  Lets the UI render
-- "VISA · 4123 / MASTERCARD · 877 / …" chips alongside the CC_NUMBER tag.
ALTER TABLE pii_findings
    ADD COLUMN IF NOT EXISTS provider_breakdown JSONB;

CREATE TABLE IF NOT EXISTS run_log (
    log_id            BIGSERIAL PRIMARY KEY,
    phase             TEXT NOT NULL,
    scope_type        TEXT NOT NULL,
    scope_id          BIGINT,
    status            TEXT NOT NULL,
    started_at        TIMESTAMPTZ DEFAULT now(),
    ended_at          TIMESTAMPTZ,
    error_message     TEXT,
    metadata          JSONB,
    UNIQUE (phase, scope_type, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_run_log_phase_status
    ON run_log (phase, status);

-- Phase 4b: composite (multi-column) foreign keys.  Lives alongside the
-- single-column relationships table; never replaces it.  child_columns and
-- parent_columns are positionally aligned JSONB arrays of column names.
CREATE TABLE IF NOT EXISTS composite_relationships (
    composite_id      BIGSERIAL PRIMARY KEY,
    child_table_id    BIGINT NOT NULL REFERENCES tbl_inventory,
    parent_table_id   BIGINT NOT NULL REFERENCES tbl_inventory,
    child_columns     JSONB NOT NULL,   -- ["col1","col2"]
    parent_columns    JSONB NOT NULL,
    containment_full  REAL,
    cardinality       TEXT,
    name_similarity   REAL,
    discovered_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (child_table_id, parent_table_id, child_columns, parent_columns)
);

CREATE INDEX IF NOT EXISTS idx_composite_relationships_child
    ON composite_relationships (child_table_id);

-- ------------------------------------------------------------------
-- PII leak detection (post-pipeline, agent A's pii_leak.py)
-- ------------------------------------------------------------------
-- For each PII direct-identifier column, the leak scan computes containment
-- against every NON-PII column using the HyperMinHash sketch in
-- col_inventory.sketch_blob.  Containments at or above the configured
-- threshold (default 0.5) are recorded here as candidate value-overlap leaks.
CREATE TABLE IF NOT EXISTS pii_leaks (
    leak_id           BIGSERIAL PRIMARY KEY,
    source_col_id     BIGINT NOT NULL REFERENCES col_inventory,
    target_col_id     BIGINT NOT NULL REFERENCES col_inventory,
    containment       REAL NOT NULL,
    leak_kind         TEXT NOT NULL DEFAULT 'value_overlap',
    detected_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_col_id, target_col_id, leak_kind),
    CHECK (leak_kind IN ('value_overlap','name_overlap'))
);

CREATE INDEX IF NOT EXISTS idx_pii_leaks_source
    ON pii_leaks (source_col_id);
CREATE INDEX IF NOT EXISTS idx_pii_leaks_target
    ON pii_leaks (target_col_id);

-- Phase 4c: polymorphic foreign keys (Rails / Django / Laravel pattern).
-- A pair of columns in the same table where one is a short STRING
-- discriminator (e.g. commentable_type) and the other is the foreign id
-- (commentable_id).  Each (discriminator_value, parent_col) combination
-- becomes one row -- so commentable_type='Post' + commentable_id ->
-- posts.id is one row, while commentable_type='Article' + commentable_id
-- -> articles.id is another.
CREATE TABLE IF NOT EXISTS polymorphic_relationships (
    poly_id           BIGSERIAL PRIMARY KEY,
    child_table_id    BIGINT NOT NULL REFERENCES tbl_inventory,
    type_col_id       BIGINT NOT NULL REFERENCES col_inventory,
    id_col_id         BIGINT NOT NULL REFERENCES col_inventory,
    discriminator_value TEXT NOT NULL,
    parent_table_id   BIGINT NOT NULL REFERENCES tbl_inventory,
    parent_col_id     BIGINT NOT NULL REFERENCES col_inventory,
    containment_full  REAL,
    confidence        REAL,
    evidence          JSONB,
    discovered_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (child_table_id, type_col_id, id_col_id, discriminator_value, parent_col_id)
);

CREATE INDEX IF NOT EXISTS idx_polymorphic_relationships_child
    ON polymorphic_relationships (child_table_id);

CREATE INDEX IF NOT EXISTS idx_polymorphic_relationships_parent
    ON polymorphic_relationships (parent_table_id);

-- Phase 4d: JSONB soft-FK relationships.  Discovers FK-shaped values
-- buried inside JSONB columns -- e.g. events.payload->>'order_id' where
-- the leaf value matches orders.id.  One row per (child_col, jsonb_path,
-- parent_col) triple.
CREATE TABLE IF NOT EXISTS jsonb_relationships (
    jsonb_id          BIGSERIAL PRIMARY KEY,
    child_col_id      BIGINT NOT NULL REFERENCES col_inventory,
    jsonb_path        TEXT NOT NULL,    -- e.g. "$.order_id" or "$.metadata.workspace_id"
    parent_col_id     BIGINT NOT NULL REFERENCES col_inventory,
    distinct_count    BIGINT,
    containment_full  REAL,
    confidence        REAL,
    evidence          JSONB,
    discovered_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (child_col_id, jsonb_path, parent_col_id)
);

CREATE INDEX IF NOT EXISTS idx_jsonb_relationships_child
    ON jsonb_relationships (child_col_id);

CREATE INDEX IF NOT EXISTS idx_jsonb_relationships_parent
    ON jsonb_relationships (parent_col_id);

-- ---------------------------------------------------------------------------
-- Clustering phase: schema-level table clusters with archetype distribution
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS clusters (
  cluster_id             BIGSERIAL PRIMARY KEY,
  schema_name            TEXT NOT NULL,
  cluster_local_id       INTEGER NOT NULL,           -- 0-indexed within (schema_name, run)
  name                   TEXT NOT NULL,
  table_count            INTEGER NOT NULL,
  intra_edge_count       INTEGER NOT NULL,
  inter_edge_count       INTEGER NOT NULL,
  modularity_score       REAL,
  archetype_distribution JSONB NOT NULL,             -- e.g. FACT=2, DIMENSION=5, LOOKUP=1
  member_table_ids       JSONB NOT NULL,             -- list of table_id values
  generated_at           TIMESTAMPTZ DEFAULT now(),
  UNIQUE (schema_name, cluster_local_id)
);
CREATE INDEX IF NOT EXISTS idx_clusters_schema ON clusters (schema_name);
-- Idempotent additive migration: zero-shot semantic label (Sprint 3).
-- Populated only when the SentenceTransformer model is available AND the
-- cluster's centroid clears the configured similarity threshold against the
-- fixed business-domain vocabulary in domain_vocab.py.  Nullable; the UI
-- falls back to ``name`` when this is NULL.
ALTER TABLE clusters ADD COLUMN IF NOT EXISTS semantic_label TEXT;

-- Idempotent additive migration: cluster assignment columns on tbl_inventory.
ALTER TABLE tbl_inventory ADD COLUMN IF NOT EXISTS cluster_id BIGINT;
ALTER TABLE tbl_inventory ADD COLUMN IF NOT EXISTS archetype TEXT;       -- FACT|DIMENSION|LOOKUP|JUNCTION|AUDIT
ALTER TABLE tbl_inventory ADD COLUMN IF NOT EXISTS junction_collapsed BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_tbl_inventory_cluster ON tbl_inventory (cluster_id);

-- ---------------------------------------------------------------------------
-- Data-quality findings (architect-review Phase 5b).
-- ---------------------------------------------------------------------------
-- One row per (column, issue_type) pair.  Issue types live in a controlled
-- vocabulary owned by ``discovery.data_quality.IssueType`` so the UI can
-- render stable severity colors and copy.
--   issue_type   : NULL_HEAVY | DUPLICATE_PK | LEADING_TRAILING_WHITESPACE
--                  | EMPTY_STRING | MIXED_CASE | LOW_CARDINALITY (etc.)
--   severity     : HIGH | MEDIUM | LOW
--   count        : raw count of offending rows in the sample
--   sample_rows  : total rows scanned (lets the UI compute fraction)
--   fraction     : count / sample_rows, pre-computed for sort/filter
--   samples      : up to 3 redacted example values that triggered the issue
CREATE TABLE IF NOT EXISTS data_quality_findings (
    finding_id   BIGSERIAL PRIMARY KEY,
    column_id    BIGINT NOT NULL REFERENCES col_inventory,
    issue_type   TEXT NOT NULL,
    severity     TEXT NOT NULL,
    count        BIGINT NOT NULL,
    sample_rows  BIGINT NOT NULL,
    fraction     REAL NOT NULL,
    samples      JSONB,
    detected_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (column_id, issue_type)
);
CREATE INDEX IF NOT EXISTS idx_dq_column ON data_quality_findings (column_id);
CREATE INDEX IF NOT EXISTS idx_dq_severity ON data_quality_findings (severity);


-- API job-history persistence. The FastAPI backend writes through to this
-- table on every job submit / status change so that restarting the API
-- does not blank the dashboard.
CREATE TABLE IF NOT EXISTS jobs (
  job_id              TEXT PRIMARY KEY,
  label               TEXT NOT NULL,
  schema_name         TEXT NOT NULL,
  status              TEXT NOT NULL,
  submitted_at        TIMESTAMPTZ NOT NULL,
  started_at          TIMESTAMPTZ,
  ended_at            TIMESTAMPTZ,
  error_message       TEXT,
  relationships_count INTEGER,
  pii_count           INTEGER,
  cluster_count       INTEGER,
  source_host         TEXT,
  source_port         INTEGER,
  source_database     TEXT,
  source_user         TEXT,
  work_dir            TEXT,
  cfg_path            TEXT,
  log_path            TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted ON jobs (submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_schema    ON jobs (schema_name);

COMMIT;
