# API documentation

FastAPI app served by uvicorn at `http://127.0.0.1:8000`. All routes under
`/api/*`. The Angular UI proxies `/api/*` from `http://localhost:4200`.

## Auth

| Endpoint | Auth |
|---|---|
| `POST /api/jobs` | **Required** — header `X-Discovery-Token: <DISCOVERY_API_TOKEN>` |
| Every other route | Open (GET-only, MVP convention so the dashboard polls without token glue) |

CORS allowlist: `http://localhost:4200`, `http://127.0.0.1:4200`.
`allow_credentials=False`, `allow_methods=["GET","POST"]`.

Required env at boot (uvicorn refuses to start otherwise):
`SOURCE_DB_PASSWORD`, `RESULTS_DB_PASSWORD`, `DISCOVERY_API_TOKEN`.

## Endpoint reference

### `GET /api/health`

Liveness probe.

**200**:
```json
{ "status": "ok" }
```

---

### `GET /api/schemas`

Lists schemas seeded in the source DB (the one the API is configured to
connect to). Used by the Dashboard to populate the per-schema cards.

**200**:
```json
{
  "source": { "host": "localhost", "port": 5432, "database": "test", "user": "adsuser" },
  "schemas": [
    { "schema_name": "adv",       "table_count": 41 },
    { "schema_name": "saleor",    "table_count": 32 },
    { "schema_name": "dvdrental", "table_count": 15 }
  ],
  "total": 3
}
```

---

### `POST /api/jobs`

Submit a new discovery job. Auth-gated.

**Headers**: `Content-Type: application/json`, `X-Discovery-Token: <token>`

**Body** (`JobRequest`):
```json
{
  "label":    "adv smoke",       // required, non-empty
  "schema":   "adv",             // required — source schema to scan
  "host":     "localhost",       // required
  "port":     5432,              // optional, default 5432
  "database": "test",            // required — source DB name
  "user":     "adsuser",         // required
  "password": "Ads@3421"         // required, scrubbed before storing
}
```

**200** — returns the `JobStatus` (see below) with `status="queued"` or
`status="running"`. The runner has been spawned in a daemon thread.

**Side-effects**:
- New row inserted into `discovery.jobs` with the metadata.
- `discovery.run_log` and every analysis table are TRUNCATE'd so the new
  job runs the full 14-phase pipeline cleanly.

**Errors**:
- `401` if token missing/wrong.
- `400` if config building fails (e.g. unreachable extraction service).

---

### `GET /api/jobs`

List the newest 200 jobs (in-memory + persisted), newest first.

**200**: array of `JobStatus`. Same shape as `GET /api/jobs/{id}`.

---

### `GET /api/jobs/{job_id}`

Status for one job.

**200** — `JobStatus`:
```json
{
  "job_id": "8d81ac01463a",
  "label": "adv smoke",
  "schema_name": "adv",
  "status": "succeeded",                // queued|running|succeeded|failed
  "submitted_at": "2026-04-27T10:11:16Z",
  "started_at":   "2026-04-27T10:11:16Z",
  "ended_at":     "2026-04-27T10:11:31Z",
  "current_phase": null,
  "progress": {},
  "error": null,
  "relationships_count": 253,           // populated on succeed/fail
  "pii_count":           33,
  "cluster_count":        4
}
```

**404** if `job_id` not in registry.

---

### `GET /api/jobs/{job_id}/log?tail=200`

Returns the tail of the pipeline log file at `<work_dir>/run.log`.

**Query params**:
- `tail` (int, default `200`): number of trailing lines to return.

**200**:
```json
{ "log": "..." }
```

---

### `GET /api/jobs/{job_id}/relationships?limit=500`

Returns the relationship graph (single-column FKs from `relationships` UNION
composite FKs from `relationships_unified` view). Powers the
`relationship-graph` and `erd-card` components.

**200**:
```json
{
  "schema": "adv",
  "total_edges": 253,
  "total_tables": 41,
  "nodes": [
    { "id": "address", "label": "address", "value": 30000 }
  ],
  "edges": [
    {
      "from":          "address",
      "to":            "state_province",
      "label":         "state_province_id → state_province_id",
      "containment":   1.0,
      "cardinality":   "MANY_TO_ONE",
      "confidence":    0.97,
      "evidence":      { "is_a_inheritance": false, "...": "..." },
      "direction_reason": "declared PK on parent",
      "composite_columns": null
    }
  ]
}
```

**Query params**: `limit` (int, default `500`) caps the number of edges
returned.

---

### `GET /api/jobs/{job_id}/pii`

Returns all PII findings for the job's schema.

**200**:
```json
{
  "schema": "adv",
  "total":  33,
  "findings": [
    {
      "table_name":  "person",
      "column_name": "email",
      "pii_type":    "EMAIL",
      "detector":    "regex",
      "match_count":      1500,
      "sample_count":     1500,
      "match_rate":       1.0,
      "regex_match_rate": 1.0,
      "name_prior":       true,
      "score":            1.0,
      "specificity":      1,
      "validated":        true,
      "redacted_examples": ["v***@s***.com"]
    }
  ]
}
```

---

### `GET /api/jobs/{job_id}/columns`

Full column inventory for the job's schema. Used by the ERD card view to
list every column per table.

**200**:
```json
{
  "schema":         "adv",
  "tables":         ["address", "person", "..."],
  "total_tables":    41,
  "total_columns":  281,
  "columns": [
    {
      "table":     "person",
      "column":    "business_entity_id",
      "ordinal":   1,
      "data_type": "integer",
      "is_pk":     true,
      "is_fk":     false
    }
  ]
}
```

**Defensive**: if the new schema is older and lacks `archetype`/`subject_kinds`
columns on `tbl_inventory`, the missing fields are omitted gracefully (no 500).

---

### `GET /api/jobs/{job_id}/clusters`

Cluster overview — one row per cluster + cross-cluster edge counts. Powers
the Clusters tab on JobDetail.

**200**:
```json
{
  "schema":              "adv",
  "total_clusters":       4,
  "modularity":           0.296,
  "junctions_collapsed":  0,
  "clusters": [
    {
      "cluster_id":         0,
      "name":               "sales_order_header_cluster",
      "table_count":        14,
      "intra_edges":        18,
      "inter_edges":         4,
      "archetype_distribution": { "FACT": 6, "DIMENSION": 5, "LOOKUP": 3 },
      "modularity_contribution": 0.21,
      "pii_table_count":    5,
      "subject_kinds":      ["EMAIL", "PHONE"]
    }
  ],
  "cluster_edges": [
    { "from": 0, "to": 1, "count": 12 }
  ]
}
```

`cluster_edges` lists every pair of clusters with at least one cross-cluster
FK edge. Powers the macro view in the cluster graph (and the columnar view's
adjacency layout).

---

### `GET /api/jobs/{job_id}/clusters/{cluster_local_id}`

Detail for one cluster. `cluster_local_id` is the 0-indexed value within the
schema (NOT the BIGSERIAL PK).

**200**:
```json
{
  "cluster_id":  0,
  "name":        "sales_order_header_cluster",
  "tables": [
    {
      "table_id":      123,
      "table_name":    "customers",
      "row_count":     1500,
      "archetype":     "DIMENSION",
      "subject_kinds": ["EMAIL", "PHONE"]
    }
  ],
  "edges": [
    {
      "from":          "orders",
      "to":            "customers",
      "child_column":  "customer_id",
      "parent_column": "id",
      "confidence":    0.99,
      "cardinality":   "MANY_TO_ONE"
    }
  ],
  "pii_findings": [
    {
      "table_name":  "customers",
      "column_name": "email",
      "pii_type":    "EMAIL",
      "score":       1.0,
      "validated":   true
    }
  ],
  "bridge_tables": [
    {
      "table_name":      "person",
      "to_cluster_id":   1,
      "to_cluster_name": "business_entity_cluster"
    }
  ],
  "cross_cluster_edges": [
    {
      "from":          "customer",
      "to":            "person",
      "child_column":  "person_id",
      "parent_column": "business_entity_id",
      "confidence":    1.0,
      "cardinality":   "MANY_TO_ONE"
    }
  ]
}
```

`bridge_tables` are tables OUTSIDE this cluster that have an FK edge into
or out of a member — the "super-points" rendered in the per-cluster ERD as
ghost cards.

`cross_cluster_edges` lists every FK edge with exactly one endpoint in
this cluster.

**404** if `cluster_local_id` doesn't exist for the schema.

---

### `GET /api/jobs/{job_id}/summary`

Aggregate KPIs for the job. Used by the Dashboard sparkline + per-schema
card stats.

**200**:
```json
{
  "job_id":       "8d81ac01463a",
  "schema_name":  "adv",
  "tables":        41,
  "rows_total":    404017,
  "relationships_count": 253,
  "pii_findings_count":   33,
  "duration_seconds":     14.3,
  "phase_complete": [
    "inventory","extract","fingerprint","pii_scan","candidate_gen","validate",
    "composite_fk","polymorphic_fk","jsonb_fk","inheritance",
    "pii_propagation","pii_leak","clustering","report"
  ],
  "cluster_count":     4,
  "clusters_by_size": [
    { "cluster_id": 0, "name": "sales_order_header_cluster", "table_count": 14 }
  ],
  "expected_fks": 54,                // present only when /tmp/check_<schema>_recall.py is loaded
  "matched_fks":  54,
  "recall":       1.0,
  "precision":    0.196
}
```

`expected_fks`/`matched_fks`/`recall`/`precision` are populated only for
schemas where the `EXPECTED_FKS` dict in `main.py` has a known truth list.

---

## Data shapes (TypeScript reference)

```ts
type JobStatus = {
  job_id: string;
  label: string;
  schema_name: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  submitted_at: string;     // ISO 8601
  started_at?: string;
  ended_at?: string;
  current_phase?: string;
  progress?: Record<string, unknown>;
  error?: string;
  relationships_count?: number;
  pii_count?: number;
  cluster_count?: number;
};

type RelationshipGraph = {
  schema: string;
  total_edges: number;
  total_tables: number;
  nodes: { id: string; label: string; value: number }[];
  edges: {
    from: string;
    to: string;
    label: string;
    containment: number | null;
    cardinality: 'ONE_TO_ONE' | 'ONE_TO_MANY' | 'MANY_TO_ONE' | 'MANY_TO_MANY';
    confidence: number | null;
    evidence?: Record<string, unknown>;
    direction_reason?: string;
    composite_columns?: string[];
  }[];
};

type Cluster = {
  cluster_id: number;
  name: string;
  table_count: number;
  intra_edges: number;
  inter_edges: number;
  archetype_distribution: Record<string, number>;
  modularity_contribution: number;
  pii_table_count: number;
  subject_kinds: string[];
};

type ClusterDetail = {
  cluster_id: number;
  name: string;
  tables: { table_id: number; table_name: string; row_count: number;
            archetype: string; subject_kinds: string[] | null }[];
  edges:  { from: string; to: string; child_column: string;
            parent_column: string; confidence: number; cardinality: string }[];
  pii_findings: { table_name: string; column_name: string; pii_type: string;
                  score: number; validated: boolean }[];
  bridge_tables?:       { table_name: string; to_cluster_id: number | null;
                          to_cluster_name: string }[];
  cross_cluster_edges?: { from: string; to: string; child_column: string;
                          parent_column: string; confidence: number;
                          cardinality: string }[];
};
```

## Error envelope

FastAPI default — all errors are JSON `{ "detail": "<message>" }` with
appropriate HTTP status:

| Status | Cause |
|---|---|
| 400 | Bad request (e.g. config build failed) |
| 401 | Missing/invalid `X-Discovery-Token` on `POST /api/jobs` |
| 404 | `job_id` / `cluster_local_id` not found |
| 422 | Pydantic validation error on the request body |
| 503 | `discovery.jobs` table not yet created (run `discovery init` once) |

## Quick test

```bash
# Liveness
curl -s http://127.0.0.1:8000/api/health

# Submit a job (auth-gated)
curl -s -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -H "X-Discovery-Token: dev-secret" \
  -d '{
    "label":"adv smoke",
    "schema":"adv",
    "host":"localhost","port":5432,
    "database":"test","user":"adsuser","password":"Ads@3421"
  }'

# Poll status
curl -s http://127.0.0.1:8000/api/jobs/<job_id>/summary
```
