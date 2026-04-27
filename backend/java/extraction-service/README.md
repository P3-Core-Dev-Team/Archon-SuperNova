# Discovery Extraction Service

**Extract data from a Postgres source via REST API, write directly to Parquet.**

A Spring Boot 3.2 monolith (Java 17) that owns the source database
connection, query whitelisting and credential management. The Python
discovery pipeline calls this service over HTTP and never opens its own
database connections.

## Stack

- **Java 17 LTS** — platform threads (virtual threads require Java 21
  and were dropped in the downgrade documented in DECISIONS.md ADR-001)
- **Spring Boot 3.2.x** — Web framework with auto-configuration
- **HikariCP** — JDBC connection pooling (fastest implementation)
- **Apache Arrow + Parquet** — Columnar writing to Parquet format
- **Resilience4j** — One global bulkhead capping concurrent extractions
- **JSqlParser** — Server-side query whitelist validation
- **Testcontainers** — Ephemeral Postgres for integration tests

## API

All endpoints (except `/actuator/health` and `/actuator/info`) require
Bearer token authentication. The token is read from
`EXTRACTION_SERVICE_TOKEN`; the service refuses to start if it is unset
unless `EXTRACTION_AUTH_DISABLED=true` is also set.

### Extraction Endpoints

**POST /api/v1/extract** — Synchronous extraction
- Blocks until extraction is complete or timeout
- Request: `ExtractionRequest` (connection, query, output config)
- Response: `ExtractionResponse` (extraction_id, status, manifest, error)
- Manifest includes: file paths, row/byte counts, duration, throughput

### Connection Testing

**POST /api/v1/connections/test** — Validate a connection without extracting
- Request: `ConnectionConfig`
- Response: HTTP 200 (ok) or 400 (connection failed)

### Operations

**GET /actuator/health** — Spring Boot health check (open, no auth)

**GET /actuator/prometheus** — Prometheus metrics (bearer-protected)

See `src/main/resources/application.yml` for all configuration options
and `openapi/extraction-service-v1.yaml` for the full wire contract.

## Run Locally

```bash
cd extraction-service
EXTRACTION_SERVICE_TOKEN=dev-token mvn spring-boot:run
```

Starts on `http://localhost:8080`. Output Parquet files are written under
`STORAGE_PATH` (default `/data/parquet`).

## Configuration

| Env Var | Default | Purpose |
|---|---|---|
| `EXTRACTION_SERVICE_TOKEN` | (none, required) | Bearer token enforced by `SecurityFilterChain` |
| `EXTRACTION_AUTH_DISABLED` | `false` | Disables auth (dev/test only) |
| `STORAGE_PATH` | `/data/parquet` | Local directory for Parquet files |

The S3 storage backend was removed when the service was monolithised;
use any out-of-band copier (rclone, aws cli, etc.) to ship the local
Parquet files to long-term storage.
