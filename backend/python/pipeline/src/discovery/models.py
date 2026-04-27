"""
models.py — Pydantic models for the Discovery Extraction Service API.

These correspond to the OpenAPI schema defined in
openapi/extraction-service-v1.yaml.  Field names are snake_case; aliases
match the JSON property names where they differ.

Security note: ConnectionConfig.password_secret_ref holds a reference to a
secret (env://VAR or vault://path), NEVER the resolved password value.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Connection & extraction request
# ---------------------------------------------------------------------------


class ConnectionConfig(BaseModel):
    """
    Connection descriptor sent to the extraction service.

    Two channels for the source-DB credential:
      * ``password_secret_ref`` — production path (env:// or vault://). The
        extraction service resolves the reference; Python never sees the
        actual value.
      * ``password_inline`` — dev / per-job path. The API may set this when
        the user supplies the credential through the submit form so the
        password is delivered with the request rather than depending on a
        process-wide env var on the extraction service. NEVER logged.
    """

    type: Literal["postgres"]
    host: str
    port: int
    database: str
    user: str
    password_secret_ref: str = Field(
        description="Secret reference: env://VAR_NAME or vault://path/to/secret"
    )
    password_inline: Optional[str] = Field(
        default=None,
        description=(
            "Optional literal password (dev / per-job override). When set, "
            "the extraction service uses this value directly and ignores "
            "password_secret_ref. NEVER logged."
        ),
    )
    ssl_mode: Literal["disable", "require", "verify-ca", "verify-full"] = "require"
    application_name: str = "discovery-extractor"

    model_config = ConfigDict(populate_by_name=True)


class OutputConfig(BaseModel):
    """Output Parquet file settings."""

    path: str = Field(
        description=(
            "Local filesystem path; absolute or relative to the service's "
            "STORAGE_PATH"
        )
    )
    compression: Literal["zstd", "snappy", "gzip", "none"] = "zstd"
    compression_level: int = 3
    row_group_size: int = 100_000
    page_size: int = 1_048_576


class ExtractionOptions(BaseModel):
    """Optional per-request extraction tunables."""

    fetch_size: int = 10_000
    # 7200 seconds (2h) — chosen to match the client's request_timeout_seconds
    # default and accommodate large tables.  The OpenAPI default is 3600 but
    # the pipeline uses the longer value to avoid server-side timeouts.
    timeout_seconds: int = 7_200
    max_rows: int | None = None
    tag: str | None = Field(default=None, description="Free-form caller tag for tracing")


class ExtractionRequest(BaseModel):
    """Full extraction request payload."""

    connection: ConnectionConfig
    query: str = Field(
        description=(
            "SELECT query to execute.  Service whitelists: "
            "SELECT [cols|*] FROM <table_or_view>. "
            "JOIN, GROUP BY, aggregate functions, DISTINCT, subqueries, CTEs are forbidden."
        )
    )
    output: OutputConfig
    options: ExtractionOptions | None = None


# ---------------------------------------------------------------------------
# Extraction response / status
# ---------------------------------------------------------------------------


class ExtractionStatus(str, Enum):
    """Possible extraction lifecycle states.

    The new contract has only sync extraction (no async/cancel), so the wire
    statuses are restricted to the running/completed/failed lifecycle.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ManifestEntry(BaseModel):
    """One Parquet file produced during an extraction."""

    path: str
    rows: int = Field(description="Row count in this file")
    # Pydantic field name `bytes` shadows the builtin at instance level; the
    # OpenAPI wire name is "bytes" so we keep the field name to match.
    bytes: int = Field(description="File size in bytes")
    checksum_sha256: str | None = None
    row_groups: int | None = None

    model_config = ConfigDict(populate_by_name=True)


class ExtractionManifest(BaseModel):
    """Summary of all files produced by an extraction job."""

    files: list[ManifestEntry] = Field(default_factory=list)
    duration_ms: int | None = None
    rows_per_second: int | None = None
    bytes_per_second: int | None = None

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)

    @property
    def total_rows(self) -> int:
        return sum(f.rows for f in self.files)


class ErrorInfo(BaseModel):
    """Error payload returned by the extraction service."""

    code: str
    message: str
    retryable: bool = False


class ExtractionResponse(BaseModel):
    """
    Full status + manifest response from /api/v1/extract.
    """

    extraction_id: str
    status: ExtractionStatus
    manifest: ExtractionManifest | None = None
    error: ErrorInfo | None = None
