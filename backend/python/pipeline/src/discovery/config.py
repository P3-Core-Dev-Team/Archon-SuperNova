"""
config.py — AppConfig via pydantic-settings.

Loads config/default.yaml as the base; environment variables override via
${VAR:default} interpolation applied BEFORE Pydantic sees the values.

Design notes:
  - SourceDbConfig wraps ConnectionConfig fields plus a 'schemas' list that
    is pipeline-specific (not in the OpenAPI ConnectionConfig).  A helper
    .to_connection_config() returns a clean ConnectionConfig to pass to the
    extraction service.
  - results_db uses a real 'password' field (Python connects there directly).
  - source_db uses 'password_secret_ref' — Python never sees the real password.
  - Env-var interpolation: load_config() does a pre-pass regex substitution
    over the raw YAML text before parsing, resolving ${NAME} and ${NAME:default}
    from os.environ.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from discovery.models import ConnectionConfig

# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def _interpolate(text: str) -> str:
    """
    Replace ``${VAR}`` and ``${VAR:default}`` tokens with os.environ values.

    Behaviour
    ---------
    * ``${VAR}``   — VAR must be set in the environment, else
                    ``EnvironmentError`` is raised.
    * ``${VAR:default}`` — uses VAR if set, else *default* (which may be
                          empty string).

    Required-without-default vars include any pipeline secret reference; the
    function makes no special-cases — every bare ``${VAR}`` is required.
    """

    missing: list[str] = []

    def replacer(m: re.Match) -> str:  # type: ignore[type-arg]
        var_name, default = m.group(1), m.group(2)
        value = os.environ.get(var_name)
        if value is not None:
            return value
        if default is not None:
            return default
        missing.append(var_name)
        return m.group(0)

    result = _ENV_PATTERN.sub(replacer, text)
    if missing:
        unique_missing = sorted(set(missing))
        raise EnvironmentError(
            "Required environment variable(s) missing during config "
            f"interpolation: {', '.join(unique_missing)}.  "
            "Provide a default in the YAML (${VAR:default}) or set the variable."
        )
    return result


# ---------------------------------------------------------------------------
# Sub-config models
# ---------------------------------------------------------------------------


class ExtractionServiceConfig(BaseModel):
    base_url: str = "http://localhost:8080"
    auth_token: str = Field(description="Bearer token for extraction service auth")
    request_timeout_seconds: int = 7200
    retry_attempts: int = 3
    retry_backoff_seconds: int = 5


class SourceDbConfig(BaseModel):
    """
    Source database descriptor.

    Only the fields required by ConnectionConfig are forwarded to the service.
    'schemas' is a pipeline-only field used during inventory to scope which
    schemas to enumerate.

    password_secret_ref is a reference (env://VAR or vault://path) — Python
    never resolves this to an actual password.
    """

    type: Literal["postgres", "mysql", "sqlserver", "oracle"] = "postgres"
    host: str = Field(description="Source DB hostname")
    port: int = 5432
    database: str = Field(description="Source DB name")
    user: str = Field(description="Source DB user")
    password_secret_ref: str = Field(
        description="Secret ref: env://VAR or vault://path.  NEVER the actual password."
    )
    # Optional dev / per-job override.  When set, the extraction service uses
    # this value directly and ignores ``password_secret_ref``.  The API writes
    # this when the user supplies a credential via the submit form.
    password_inline: Optional[str] = None
    schemas: list[str] = Field(default_factory=lambda: ["public"])
    ssl_mode: Literal["disable", "require", "verify-ca", "verify-full"] = "require"
    application_name: str = "discovery-extractor"

    def to_connection_config(self) -> ConnectionConfig:
        """
        Return a ConnectionConfig suitable for sending to the extraction service.
        Pipeline-only fields (schemas) are excluded.
        """
        return ConnectionConfig(
            type=self.type,
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password_secret_ref=self.password_secret_ref,
            password_inline=self.password_inline,
            ssl_mode=self.ssl_mode,
            application_name=self.application_name,
        )


class ResultsDbConfig(BaseModel):
    """Connection config for the Postgres results DB that Python owns directly."""

    host: str = "localhost"
    port: int = 5432
    database: str = "discovery_results"
    user: str = "postgres"
    password: str = Field(description="Results DB password (Python connects here)")
    schema_name: str = Field(default="discovery", alias="schema")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def dsn(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class StorageConfig(BaseModel):
    base_path: str = "/data/parquet"
    duckdb_temp_dir: str = "/data/duckdb_tmp"
    duckdb_memory_limit: str = "32GB"
    parquet_cap_bytes: int = 268_435_456_000  # 250 GB soft cap


class WorkersConfig(BaseModel):
    """
    Per-phase worker counts.

    Field names map 1:1 to phase names with one exception:
    ``validate_workers`` is the Python attribute, but the YAML key is
    ``validate`` (via ``alias=``).  This avoids the Pydantic v2 UserWarning
    raised when a model field shadows ``BaseModel.validate``.

    Code reading the value should use ``cfg.validate_workers``.  Code
    constructing a value programmatically may pass ``validate=N`` (alias) or
    ``validate_workers=N`` (Python name) — both are accepted thanks to
    ``populate_by_name=True`` below.
    """

    model_config = ConfigDict(populate_by_name=True)

    extract: int = 8
    fingerprint: int = 16
    pii_scan: int = 16
    validate_workers: int = Field(
        default=8,
        alias="validate",
        description=(
            "Validation phase worker count.  YAML key: 'validate'; "
            "Python attr: 'validate_workers' (renamed to avoid shadowing "
            "BaseModel.validate in Pydantic v2)."
        ),
    )


class OrchestrationConfig(BaseModel):
    workers: WorkersConfig = Field(default_factory=WorkersConfig)
    retry_max_attempts: int = 3
    retry_backoff_seconds: int = 5
    # ----------------------------------------------------------------
    # Adaptive batching knobs — how many tasks the worker pools submit
    # to ``Pool.map()`` per generation.  Today's one-shot
    # ``pool.map(_pii_task, tasks)`` materialises every parquet handle
    # / regex matcher / DuckDB connection at once; on >1000-table
    # schemas that's a memory cliff.  Chunking caps peak memory at
    # ``chunk_size * per_worker_overhead``.
    #
    # Defaults match the reference project's pageSize values (50 / 100).
    # Set to <=0 to disable batching (one-shot, original behaviour).
    # ----------------------------------------------------------------
    pii_batch_size: int = Field(
        default=50,
        description=(
            "Columns per Pool.map generation in phase 3b (pii_scan). "
            "<=0 disables batching."
        ),
    )
    validate_batch_size: int = Field(
        default=100,
        description=(
            "Parent-column groups per Pool.map generation in phase 5 "
            "(validate). <=0 disables batching."
        ),
    )
    # ----------------------------------------------------------------
    # Stage-level fallback policy.  When True (default), phases that
    # encounter an unhandled exception log ``phase_degraded`` at WARN
    # and continue; subsequent phases see partial-or-empty inputs but
    # still run.  When False, the original fail-fast behaviour applies
    # — useful for CI / debugging.  Honoured by ``discovery.fallbacks
    # .safe_phase`` and the orchestrator's per-phase wrappers.
    # ----------------------------------------------------------------
    enable_phase_fallbacks: bool = True


class ExtractionConfig(BaseModel):
    """
    Phase-2 (extraction) tuning knobs.

    These are wholly Python-side knobs — the extraction *service* connection
    parameters live on :class:`ExtractionServiceConfig`.  This block lets
    operators control how Python builds the SELECT clauses and other extraction
    behaviours that are not part of the service contract.
    """

    # When True, the Phase-2 SELECT projects only the FK-eligible / fingerprintable
    # columns determined by inventory; when False the SQL falls back to ``SELECT *``
    # (legacy mode, useful for debugging).  Read by
    # :func:`extraction.build_select_clause`.
    column_projection: bool = True


class FingerprintConfig(BaseModel):
    sketcher: str = "hyperminhash"
    num_buckets: int = 1024
    bits_per_bucket: int = 8
    hash_algorithm: str = "xxh3_64"
    hll_p: int = 14
    exact_distinct_below: int = 10_000
    # Adaptive HLL early-stop: if relative cardinality delta drops below this
    # threshold for two consecutive row groups (after at least 3 read), abort
    # further row-group reads. 0.005 = 0.5%.
    early_stop_delta: float = 0.005


class PiiDetectorsConfig(BaseModel):
    hyperscan: bool = True
    detect_secrets: bool = True
    luhn_validation: bool = True
    stdnum_validators: list[str] = Field(
        default_factory=lambda: ["iban", "us_ssn", "uk_nhs", "vat"]
    )
    # spaCy NER pass.  Default-on: when the spacy library + en_core_web_sm
    # model are available, STRING_LONG columns get an NER scan (PERSON / GPE
    # / ORG / LOC / DATE).  If the model is missing, the scanner logs a
    # one-line warning and silently degrades to regex-only — no error.
    spacy_ner: bool = True


class PiiConfig(BaseModel):
    scan_rows_per_column: int = 50_000
    detectors: PiiDetectorsConfig = Field(default_factory=PiiDetectorsConfig)
    match_rate_threshold: float = 0.05
    redact_examples: bool = True
    fallback_engine: str = "regex"
    # Post-Phase-5 sub-phases.
    propagation_enabled: bool = True
    leak_scan_enabled: bool = True


class RelationshipsConfig(BaseModel):
    parent_distinct_ratio_min: float = 0.95
    child_min_distinct_count: int = 100
    containment_threshold: float = 0.95
    lsh_threshold: float = 0.7
    lsh_num_perm: int = 256
    faiss_index_type: str = "IndexBinaryFlat"
    # When True (default), parent-side candidates are restricted to columns whose
    # owning table has at least one PK / unique-indexed column; tables with no
    # such metadata are demoted to the ``advisory_lowconf`` tier rather than
    # silently rejected.  Read by :mod:`candidates`.
    require_parent_pk: bool = True
    # When True (default), Phase 5 (validation) restricts execution to candidates
    # marked ``tier='primary'`` — ``advisory_lowconf`` rows are written by Phase 4
    # but skipped by the validator.  This is the A5/M2 optimisation that brings
    # the working set from ~258K to ~5K candidates.  Read by :mod:`validate`.
    validate_only_primary_tier: bool = True

    # ------------------------------------------------------------------
    # Tier 1+2 accuracy improvements (cheap, recall-improving)
    # ------------------------------------------------------------------
    plural_name_normalize: bool = Field(
        default=True,
        description=(
            "Normalize plural/singular table names when scoring name similarity "
            "(e.g. 'orders' ↔ 'order') for FK candidate generation."
        ),
    )
    pii_filter_enabled: bool = Field(
        default=True,
        description="Skip PII columns from FK candidates.",
    )
    one_implicit_pk_per_table: bool = Field(
        default=True,
        description="Select a single best implicit PK per parent table.",
    )
    reverse_direction_reconciliation: bool = Field(
        default=True,
        description=(
            "Detect and reconcile candidate pairs where the inferred parent/child "
            "direction is reversed; keep only the more plausible direction."
        ),
    )
    top_k_per_child: int = Field(
        default=10,
        description=(
            "Maximum number of parent candidates retained per child column "
            "after confidence-based ranking.  Raised from 5 → 10 in the "
            "Sprint A7 recall fix: with K=5, child columns whose true parent "
            "is a 'popular' table (e.g. ``employees.id``, referenced by many "
            "FKs) frequently saw the real parent ranked 6+ by confidence and "
            "demoted to advisory.  10 is generous but precision is "
            "now sustained by the other gates."
        ),
    )
    dense_serial_hard_reject: bool = Field(
        default=True,
        description=(
            "Hard-reject candidate pairs where the child column is a dense "
            "serial / sequence (i.e. clearly an autoincrement, not a FK)."
        ),
    )
    range_overlap_gate_enabled: bool = Field(
        default=True,
        description=(
            "Enable the [min,max] range-overlap gate that quickly rejects "
            "child↔parent pairs whose value ranges cannot intersect."
        ),
    )
    max_relationships: int | None = Field(
        default=None,
        description=(
            "Global cap on number of relationships emitted.  None = no cap; "
            "an integer truncates to that many after final ranking."
        ),
    )

    # ------------------------------------------------------------------
    # Tier 3 — semantic name similarity (opt-in)
    # ------------------------------------------------------------------
    semantic_name_similarity: bool = Field(
        default=True,
        description=(
            "Enable semantic-embedding name similarity.  Default True since "
            "Sprint A8: warmup happens once at Phase-4 start; if "
            "sentence-transformers / the model are not installed, the helper "
            "logs a warning and silently falls back to lexical similarity."
        ),
    )
    semantic_min_score: float = Field(
        default=0.5,
        description=(
            "Minimum cosine similarity from the semantic encoder for a "
            "candidate to count as a name match."
        ),
    )

    # ------------------------------------------------------------------
    # Composite FK detection (Phase 4b).  Default ON: composite is now
    # part of the standard ``run-all`` pipeline.  Set to False to skip
    # the phase entirely (useful when only single-column FKs are needed).
    # ------------------------------------------------------------------
    composite_fk_enabled: bool = Field(
        default=True,
        description="Enable composite (multi-column) FK detection (Phase 4b).",
    )
    composite_fk_max_arity: int = Field(
        default=3,
        description="Maximum arity (column count) for composite-FK candidates.",
    )
    composite_fk_min_containment: float = Field(
        default=0.95,
        description=(
            "Minimum containment ratio (child-tuples-found-in-parent / "
            "child-tuples) required to retain a composite-FK candidate."
        ),
    )

    # ------------------------------------------------------------------
    # Polymorphic FK detection (Phase 4c).  Detects Rails / Django /
    # Laravel patterns: ``commentable_type`` + ``commentable_id`` etc.
    # ------------------------------------------------------------------
    polymorphic_fk_enabled: bool = Field(
        default=True,
        description="Enable polymorphic FK detection (Phase 4c).",
    )
    polymorphic_min_containment: float = Field(
        default=0.95,
        description=(
            "Minimum containment for a (discriminator_value, parent) match "
            "to be persisted as a polymorphic FK row."
        ),
    )
    polymorphic_max_discriminator_distinct: int = Field(
        default=20,
        description=(
            "Maximum distinct values in the discriminator column for it to "
            "qualify as a type tag.  Anything above this is treated as a "
            "generic short string and skipped."
        ),
    )
    polymorphic_min_partition_rows: int = Field(
        default=1,
        description=(
            "Minimum rows in a discriminator partition before it is tested "
            "for containment.  1 keeps even tiny partitions in the report."
        ),
    )

    # ------------------------------------------------------------------
    # JSONB soft-FK detection (Phase 4d).
    # ------------------------------------------------------------------
    jsonb_fk_enabled: bool = Field(
        default=True,
        description="Enable JSONB soft-FK detection (Phase 4d).",
    )
    jsonb_sample_rows: int = Field(
        default=1000,
        description=(
            "Number of rows sampled per JSONB column when extracting leaf "
            "paths.  1000 is enough to discover the dominant key set on most "
            "schemas without paying for full-column scans."
        ),
    )
    jsonb_min_containment: float = Field(
        default=0.95,
        description=(
            "Minimum containment for a (jsonb_path, parent) match to be "
            "persisted as a JSONB soft-FK row."
        ),
    )
    jsonb_min_distinct_count: int = Field(
        default=5,
        description=(
            "Minimum distinct values at a JSONB leaf path before it is "
            "treated as a candidate FK source."
        ),
    )

    # ------------------------------------------------------------------
    # Inheritance (is-a) annotator -- post-step that tags relationships
    # whose pattern matches `child.pk == parent.pk` AND containment=1.0
    # AND name_sim>=0.95 with ``evidence.is_a_inheritance=true``.
    # ------------------------------------------------------------------
    inheritance_annotator_enabled: bool = Field(
        default=True,
        description="Enable the inheritance / is-a relationship annotator.",
    )
    inheritance_min_name_sim: float = Field(
        default=0.95,
        description=(
            "Minimum column-name similarity for a containment=1.0 PK<->PK "
            "pair to be tagged as an inheritance relationship."
        ),
    )

    # ------------------------------------------------------------------
    # Schema clustering — groups tables into cohesive clusters per schema
    # using graph-community detection on the validated FK graph.  Runs
    # after PII leak scan, before report generation.
    # ------------------------------------------------------------------
    clustering_enabled: bool = Field(
        default=True,
        description=(
            "Enable schema clustering phase.  Groups tables into semantically "
            "cohesive clusters based on the validated FK graph and PII findings."
        ),
    )
    # ------------------------------------------------------------------
    # Hybrid (semantic + graph) clustering refinements — see clustering.py
    # _semantic_merge() and _zero_shot_label().  Reuse the same
    # SentenceTransformer model already loaded by name_similarity.py;
    # if the model is unavailable, both passes silently no-op (graceful
    # degradation).
    # ------------------------------------------------------------------
    semantic_merge_enabled: bool = Field(
        default=True,
        description=(
            "After Louvain produces communities, optionally merge pairs of "
            "clusters whose centroid embeddings are sufficiently similar AND "
            "share inter-cluster FK edges.  Disabled => Louvain output only."
        ),
    )
    semantic_merge_threshold: float = Field(
        default=0.65,
        description=(
            "Minimum cosine similarity between two cluster centroids "
            "before they are eligible to merge.  Higher = stricter."
        ),
    )
    semantic_merge_modularity_floor: float = Field(
        default=0.95,
        description=(
            "Modularity guard: a candidate merge is REJECTED if it would "
            "drop the global modularity below this fraction of the pre-merge "
            "value.  0.95 = tolerate up to 5% modularity loss."
        ),
    )
    semantic_label_enabled: bool = Field(
        default=True,
        description=(
            "Run zero-shot domain labelling: each cluster's centroid is "
            "compared to a fixed business-domain vocabulary and tagged "
            "with the closest match (e.g. 'Sales', 'Customer Management')."
        ),
    )
    semantic_label_threshold: float = Field(
        default=0.55,
        description=(
            "Minimum cosine similarity between a cluster centroid and a "
            "vocabulary term to attach the term as the cluster's "
            "semantic_label.  Below threshold => no label."
        ),
    )


class ReportingConfig(BaseModel):
    output_dir: str = "/data/reports"
    formats: list[str] = Field(default_factory=lambda: ["csv", "excel"])


class ObservabilityConfig(BaseModel):
    """
    Prometheus / metrics endpoint configuration.

    The metrics HTTP server is started once at the top of ``run_all`` (and
    the per-phase CLI commands).  9009 is the default scrape port; override
    via the YAML ``observability.metrics_port`` key or by setting an
    environment variable resolved during interpolation.
    """

    metrics_port: int = 9009


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    """
    Complete pipeline configuration.  Loaded from YAML + env var interpolation.
    """

    extraction_service: ExtractionServiceConfig = Field(
        default_factory=ExtractionServiceConfig
    )
    source_db: SourceDbConfig
    results_db: ResultsDbConfig
    storage: StorageConfig = Field(default_factory=StorageConfig)
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    fingerprint: FingerprintConfig = Field(default_factory=FingerprintConfig)
    pii: PiiConfig = Field(default_factory=PiiConfig)
    relationships: RelationshipsConfig = Field(default_factory=RelationshipsConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = (
    Path(__file__).parent.parent.parent.parent / "config" / "default.yaml"
)


def load_config(path: Path | None = None) -> AppConfig:
    """
    Load AppConfig from YAML file.

    Steps:
    1. Read YAML text from *path* (defaults to config/default.yaml relative to
       the pipeline root).
    2. Run ${VAR:default} interpolation against os.environ.
    3. Parse the resulting dict with Pydantic.

    Parameters
    ----------
    path:
        Explicit path to a YAML config file.  If None, uses config/default.yaml
        relative to the pipeline package root.

    Returns
    -------
    AppConfig

    Raises
    ------
    FileNotFoundError: if the config file does not exist.
    pydantic.ValidationError: if required fields are missing or invalid.
    """
    config_path = path if path is not None else _DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}.  "
            "Set --config <path> or ensure config/default.yaml exists."
        )

    raw_text = config_path.read_text(encoding="utf-8")
    interpolated = _interpolate(raw_text)
    data: dict[str, Any] = yaml.safe_load(interpolated) or {}

    return AppConfig.model_validate(data)
