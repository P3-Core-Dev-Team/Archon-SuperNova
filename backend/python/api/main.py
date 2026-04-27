"""
FastAPI backend for the Discovery UI.

Endpoints:
    POST   /api/jobs                  submit a new discovery job
    GET    /api/jobs                  list jobs
    GET    /api/jobs/{job_id}         job detail + status
    GET    /api/jobs/{job_id}/log     job log tail
    GET    /api/jobs/{job_id}/relationships
                                       relationship graph (nodes + edges)
    GET    /api/jobs/{job_id}/pii     PII findings table

A job submission spawns the discovery CLI as a background subprocess
and tracks status in an in-memory registry. The pipeline writes to the
shared `discovery_results` Postgres DB; the API queries it on demand.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import psycopg2
import yaml
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Pipeline source directory. start.sh exports ARCHON_PIPELINE_SRC pointing at
# backend/python/pipeline/src; fall back to a path relative to this file so
# manual `uvicorn` invocations still work.
_DEFAULT_PIPELINE_SRC = Path(__file__).resolve().parents[1] / "pipeline" / "src"
PIPELINE_SRC = Path(os.environ.get(
    "ARCHON_PIPELINE_SRC",
    os.environ.get("PIPELINE_SRC", str(_DEFAULT_PIPELINE_SRC)),
))
EXTRACTION_SERVICE_URL = os.environ.get(
    "EXTRACTION_SERVICE_URL", "http://127.0.0.1:8080"
)
EXTRACTION_SERVICE_TOKEN = os.environ.get(
    "EXTRACTION_SERVICE_TOKEN", "dev-token"
)
# NOTE: SOURCE_DB_PASSWORD and RESULTS_DB_PASSWORD MUST be exported in the
# uvicorn process environment. The restart script sets them; if launching
# uvicorn manually, prefix with `SOURCE_DB_PASSWORD=... RESULTS_DB_PASSWORD=...`.
RESULTS_DB_DSN = dict(
    host=os.environ.get("RESULTS_DB_HOST", "localhost"),
    port=int(os.environ.get("RESULTS_DB_PORT", "5432")),
    dbname=os.environ.get("RESULTS_DB_NAME", "discovery_results"),
    user=os.environ.get("RESULTS_DB_USER", "adsuser"),
    password=os.environ.get("RESULTS_DB_PASSWORD", ""),
)

app = FastAPI(title="Archon-SuperNova API", version="1.0")


@app.on_event("startup")
def _verify_required_env() -> None:
    """Fail fast if the env vars carrying DB passwords aren't set."""
    missing = [
        name for name in ("SOURCE_DB_PASSWORD", "RESULTS_DB_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            f"Required environment variables not set: {', '.join(missing)}. "
            "Export them before starting uvicorn."
        )


@app.on_event("startup")
def _bootstrap_jobs_from_db() -> None:
    """Load persisted jobs into the in-memory registry on backend boot.
    Without this, restarting uvicorn would blank the dashboard's history list.
    """
    _load_jobs_from_db()


# CORS: only allow the Angular dev server origins. allow_credentials stays
# False (no auth cookies in play). Methods restricted to GET + POST.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --- FIX-B4: dev shared-secret guard ---------------------------------------
# Tiny dependency that requires the X-Discovery-Token header on POST /api/jobs.
# Token compared with DISCOVERY_API_TOKEN from env. NOT a real secret -- its
# only purpose is to keep random web-origin pages from triggering subprocess
# pipeline runs. Fine to ship the value as a constant in client code (MVP).
from fastapi import Header  # noqa: E402  (local import: keep diff scoped)


def _require_secret(
    x_discovery_token: Optional[str] = Header(default=None),
) -> None:
    expected = os.environ.get("DISCOVERY_API_TOKEN", "")
    if not expected or x_discovery_token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing token")


# ---------------------------------------------------------- Pydantic models

class JobRequest(BaseModel):
    """Submitted by the UI form."""
    label: str = Field(..., description="Friendly job label, e.g. 'AdventureWorks'")
    host: str
    port: int = 5432
    database: str
    user: str
    password: str
    schema_name: str = Field(..., alias="schema")

    model_config = {"populate_by_name": True}


class ConnectionTestRequest(BaseModel):
    """Submitted by the UI's "Test connection" button before job submit."""
    host: str
    port: int = 5432
    database: str
    user: str
    password: str
    schema_name: str = Field(..., alias="schema")

    model_config = {"populate_by_name": True}


class JobStatus(BaseModel):
    job_id: str
    label: str
    schema_name: str
    status: str           # queued | running | succeeded | failed
    submitted_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    current_phase: Optional[str] = None
    progress: dict[str, Any] = {}
    error: Optional[str] = None
    relationships_count: Optional[int] = None
    pii_count: Optional[int] = None
    cluster_count: Optional[int] = None


# ---------------------------------------------------------- registry (in-memory + DB write-through)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------- job persistence

_JOB_DB_FIELDS = (
    "job_id", "label", "schema_name", "status", "submitted_at",
    "started_at", "ended_at", "error_message",
    "relationships_count", "pii_count", "cluster_count",
    "source_host", "source_port", "source_database", "source_user",
    "work_dir", "cfg_path", "log_path",
)


def _job_to_db_row(job: dict) -> dict:
    """Project the in-memory job dict to a row for the `jobs` table.

    Note: ``error`` field on the in-memory side maps to ``error_message`` on
    the DB side (avoid clashing with reserved words in some clients).  The
    ``req`` Pydantic object is unpacked into source_host/port/database/user.
    """
    req = job.get("req")
    return {
        "job_id":        job["job_id"],
        "label":         job["label"],
        "schema_name":   job["schema_name"],
        "status":        job["status"],
        "submitted_at":  job["submitted_at"],
        "started_at":    job.get("started_at"),
        "ended_at":      job.get("ended_at"),
        "error_message": job.get("error"),
        "relationships_count": job.get("relationships_count"),
        "pii_count":     job.get("pii_count"),
        "cluster_count": job.get("cluster_count"),
        "source_host":     getattr(req, "host", None),
        "source_port":     getattr(req, "port", None),
        "source_database": getattr(req, "database", None),
        "source_user":     getattr(req, "user", None),
        "work_dir":      str(job.get("work_dir") or "") or None,
        "cfg_path":      str(job.get("cfg_path") or "") or None,
        "log_path":      str(job.get("log_path") or "") or None,
    }


def _persist_job(job: dict) -> None:
    """UPSERT one job row to discovery.jobs.  Best-effort -- failure logs
    but does not break the in-memory job lifecycle (we don't want a DB
    hiccup to crash the pipeline runner)."""
    row = _job_to_db_row(job)
    cols = list(row.keys())
    vals = [row[c] for c in cols]
    placeholders = ", ".join(["%s"] * len(cols))
    update_set = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in cols if c != "job_id"
    )
    sql = (
        "INSERT INTO discovery.jobs ("
        + ", ".join(cols)
        + f") VALUES ({placeholders}) "
        + f"ON CONFLICT (job_id) DO UPDATE SET {update_set}"
    )
    try:
        conn = psycopg2.connect(**RESULTS_DB_DSN)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql, vals)
        finally:
            conn.close()
    except Exception as exc:
        print(f"[persist_job_failed] job_id={row['job_id']} error={exc}", flush=True)


def _load_jobs_from_db() -> None:
    """Repopulate the in-memory `_jobs` dict from `discovery.jobs` at
    backend startup.  Newest 200 jobs only (UI shows ~recent N).

    Persisted columns map back to in-memory keys.  ``req`` (the original
    JobRequest with secrets) is NOT persisted -- we synthesize a stub that
    has just the non-secret source fields for display purposes.
    """
    try:
        conn = psycopg2.connect(**RESULTS_DB_DSN)
    except Exception as exc:
        print(f"[load_jobs_skipped] db_connect_failed error={exc}", flush=True)
        return
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT to_regclass('discovery.jobs')")
                if cur.fetchone()[0] is None:
                    print("[load_jobs_skipped] jobs_table_missing", flush=True)
                    return
            except Exception:
                return
            cur.execute(
                f"SELECT {', '.join(_JOB_DB_FIELDS)} "
                "FROM discovery.jobs ORDER BY submitted_at DESC LIMIT 200"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    loaded = 0
    for row in rows:
        d = dict(zip(_JOB_DB_FIELDS, row))
        # Synthesize a tiny req-like object so list_jobs() can render hosts.
        class _StubReq:
            host     = d.get("source_host")
            port     = d.get("source_port")
            database = d.get("source_database")
            user     = d.get("source_user")
            password = ""
            label       = d["label"]
            schema_name = d["schema_name"]
        from pathlib import Path as _P
        # Crash-recovery: a job that was 'running' when the API died is
        # surfaced as 'failed' rather than left as a permanent zombie.
        status = d["status"]
        if status in ("running", "queued"):
            status = "failed"
            d["error_message"] = (d.get("error_message") or
                                  "API restarted while job was in flight")
        job = {
            "job_id": d["job_id"],
            "label":  d["label"],
            "schema_name": d["schema_name"],
            "req":    _StubReq(),
            "work_dir": _P(d["work_dir"]) if d.get("work_dir") else None,
            "cfg_path": _P(d["cfg_path"]) if d.get("cfg_path") else None,
            "log_path": _P(d["log_path"]) if d.get("log_path") else None,
            "submitted_at": d["submitted_at"],
            "started_at":   d.get("started_at"),
            "ended_at":     d.get("ended_at"),
            "status": status,
            "progress": {},
            "error":    d.get("error_message"),
            "relationships_count": d.get("relationships_count"),
            "pii_count":           d.get("pii_count"),
            "cluster_count":       d.get("cluster_count"),
        }
        _jobs[d["job_id"]] = job
        loaded += 1
    print(f"[jobs_loaded_from_db] count={loaded}", flush=True)


def _build_config(req: JobRequest, work_dir: Path) -> Path:
    """Render a discovery YAML config for one job."""
    storage = work_dir / "parquet"
    reports = work_dir / "reports"
    storage.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    cfg = {
        "extraction_service": {
            "base_url": EXTRACTION_SERVICE_URL,
            "auth_token": EXTRACTION_SERVICE_TOKEN,
            "request_timeout_seconds": 7200,
            "retry_attempts": 3,
            "retry_backoff_seconds": 2,
        },
        "source_db": {
            "type": "postgres",
            "host": req.host,
            "port": req.port,
            "database": req.database,
            "user": req.user,
            # Per-request literal password (the credential the user supplied
            # in the submit form, after the Test-connection probe succeeded).
            # The pipeline's ConnectionConfig forwards this to the extraction
            # service, which prefers it over the env:// fallback. NEVER logged.
            "password_inline": req.password,
            # Fallback secret ref kept for backward-compat — only consulted
            # when password_inline is empty (e.g. CLI-only invocations that
            # mirror the original env-var contract).
            "password_secret_ref": "env://SOURCE_DB_PASSWORD",
            "schemas": [req.schema_name],
            "ssl_mode": "disable",
        },
        "results_db": {
            "host": RESULTS_DB_DSN["host"],
            "port": RESULTS_DB_DSN["port"],
            "database": RESULTS_DB_DSN["dbname"],
            "user": RESULTS_DB_DSN["user"],
            # SQLAlchemy URL needs the @ url-encoded
            "password": RESULTS_DB_DSN["password"].replace("@", "%40"),
            "schema": "discovery",
        },
        "storage": {
            "base_path": str(storage),
            "duckdb_temp_dir": "/tmp/duckdb_tmp",
            "duckdb_memory_limit": "4GB",
            "parquet_cap_bytes": 268435456000,
        },
        "orchestration": {
            "workers": {"extract": 4, "fingerprint": 4, "pii_scan": 4, "validate": 4},
            "retry_max_attempts": 3, "retry_backoff_seconds": 5,
        },
        "fingerprint": {
            "sketcher": "hyperminhash", "num_buckets": 1024, "bits_per_bucket": 8,
            "hash_algorithm": "xxh3_64", "hll_p": 14, "exact_distinct_below": 10000,
        },
        "pii": {
            "scan_rows_per_column": 5000,
            "detectors": {"hyperscan": False, "detect_secrets": True,
                          "luhn_validation": True,
                          "stdnum_validators": ["iban", "us_ssn", "uk_nhs", "vat"]},
            "match_rate_threshold": 0.05, "redact_examples": True,
            "fallback_engine": "regex",
        },
        "relationships": {
            "parent_distinct_ratio_min": 0.95,
            "child_min_distinct_count": 5,
            "containment_threshold": 0.95,
            "lsh_threshold": 0.7, "lsh_num_perm": 256,
            "faiss_index_type": "IndexBinaryFlat",
            "require_parent_pk": True,
            "validate_only_primary_tier": True,
            "low_cardinality_name_sim_bypass": 0.85,
        },
        "reporting": {"output_dir": str(reports), "formats": ["csv", "excel"]},
    }
    cfg_path = work_dir / "config.yaml"
    with cfg_path.open("w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
    return cfg_path


def _run_pipeline(job_id: str) -> None:
    """Background job runner. Spawns `python -m discovery run-all`."""
    job = _jobs[job_id]
    req = job["req"]
    work_dir = job["work_dir"]
    cfg_path = job["cfg_path"]
    log_path = job["log_path"]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PIPELINE_SRC)
    # Note: req.password is scrubbed at submit time (FIX-B2). The pipeline
    # gets its source-DB credential via password_secret_ref: env://SOURCE_DB_PASSWORD,
    # which the mock extraction service resolves from its own environment.

    job["status"] = "running"
    job["started_at"] = _now()
    _persist_job(job)

    cmd = [
        "python3", "-m", "discovery", "run-all",
        "--config", str(cfg_path),
        "--text-logs", "--log-level", "INFO",
    ]
    try:
        with log_path.open("w") as logf:
            proc = subprocess.run(
                cmd, stdout=logf, stderr=subprocess.STDOUT, env=env,
                timeout=24 * 3600,   # 24h ceiling
            )
        if proc.returncode == 0:
            job["status"] = "succeeded"
        else:
            job["status"] = "failed"
            job["error"] = f"exit {proc.returncode}"
    except subprocess.TimeoutExpired:
        job["status"] = "failed"
        job["error"] = "timeout (24h)"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        job["ended_at"] = _now()

        # Capture topline stats from the results DB so the UI can show them.
        try:
            stats = _job_stats_from_db(req.schema_name)
            job["relationships_count"] = stats["relationships"]
            job["pii_count"] = stats["pii"]
            job["cluster_count"] = stats.get("clusters", 0)
        except Exception:
            pass
        _persist_job(job)


def _job_stats_from_db(schema_name: str) -> dict[str, int]:
    conn = psycopg2.connect(**RESULTS_DB_DSN, options="-c search_path=discovery")
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM relationships r
                JOIN col_inventory cc ON cc.column_id = r.child_col_id
                JOIN tbl_inventory ct ON ct.table_id = cc.table_id
                WHERE ct.schema_name = %s
            """, (schema_name,))
            n_rel = cur.fetchone()[0]
            cur.execute("""
                SELECT count(*) FROM pii_findings p
                JOIN col_inventory c ON c.column_id = p.column_id
                JOIN tbl_inventory t ON t.table_id = c.table_id
                WHERE t.schema_name = %s
            """, (schema_name,))
            n_pii = cur.fetchone()[0]
            # cluster_count: defensive — clusters table may not exist yet on
            # older results DBs.  Returns 0 in that case rather than 500.
            try:
                cur.execute(
                    "SELECT count(*) FROM clusters WHERE schema_name = %s",
                    (schema_name,),
                )
                n_clusters = cur.fetchone()[0]
            except Exception:
                conn.rollback()
                n_clusters = 0
            return {
                "relationships": n_rel,
                "pii": n_pii,
                "clusters": n_clusters,
            }
    finally:
        conn.close()


# ----------------------------------------------------------- HTTP routes


@app.post(
    "/api/test_connection",
    dependencies=[Depends(_require_secret)],
)
def test_connection(req: ConnectionTestRequest) -> dict[str, Any]:
    """Probe the source DB before the user submits a discovery job.

    Returns ``{ok: True, ...}`` when a basic connect + ``SELECT 1`` plus a
    schema-existence check succeed, otherwise ``{ok: False, error: <msg>}``
    with a 200 status (the UI distinguishes failure by the ``ok`` field
    rather than HTTP status, so it can show the error inline next to the
    Test-connection button).
    """
    info: dict[str, Any] = {
        "ok": False,
        "host": req.host,
        "port": req.port,
        "database": req.database,
        "schema": req.schema_name,
    }
    try:
        conn = psycopg2.connect(
            host=req.host,
            port=req.port,
            dbname=req.database,
            user=req.user,
            password=req.password,
            connect_timeout=5,
        )
    except psycopg2.OperationalError as exc:
        info["error"] = f"connect failed: {exc.args[0].strip() if exc.args else exc}"
        info["error_kind"] = "connect"
        return info
    except Exception as exc:
        info["error"] = f"connect failed: {type(exc).__name__}: {exc}"
        info["error_kind"] = "connect"
        return info

    try:
        cur = conn.cursor()
        cur.execute("SELECT version(), current_database(), current_user")
        version, dbname, current_user = cur.fetchone()
        info["server_version"] = version.split(",")[0]
        info["current_user"] = current_user

        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            (req.schema_name,),
        )
        if cur.fetchone() is None:
            info["error"] = f'schema "{req.schema_name}" not found in database "{dbname}"'
            info["error_kind"] = "schema_missing"
            return info

        cur.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            """,
            (req.schema_name,),
        )
        info["table_count"] = int(cur.fetchone()[0])
    except Exception as exc:
        info["error"] = f"probe failed: {type(exc).__name__}: {exc}"
        info["error_kind"] = "probe"
        return info
    finally:
        conn.close()

    info["ok"] = True
    return info


@app.post(
    "/api/jobs",
    response_model=JobStatus,
    dependencies=[Depends(_require_secret)],
)
def submit_job(req: JobRequest) -> JobStatus:
    """Submit a new discovery job. Spawns a background runner.

    MVP: GETs are unauthenticated (so the dashboard can poll without auth
    glue in the frontend). Only this POST is gated by X-Discovery-Token.
    """
    job_id = uuid.uuid4().hex[:12]
    work_dir = Path(tempfile.mkdtemp(prefix=f"disc-{job_id}-"))
    try:
        cfg_path = _build_config(req, work_dir)
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(400, f"config build failed: {exc}")

    # FIX-B2: scrub the password before storing in the in-memory registry.
    # _build_config has already consumed it (the YAML uses env://… refs, so
    # req.password is actually unused there too -- but be defensive).
    req_safe = req.model_copy(update={"password": ""})

    job = {
        "job_id": job_id,
        "label": req.label,
        "schema_name": req.schema_name,
        "req": req_safe,
        "work_dir": work_dir,
        "cfg_path": cfg_path,
        "log_path": work_dir / "run.log",
        "submitted_at": _now(),
        "started_at": None,
        "ended_at": None,
        "status": "queued",
        "progress": {},
        "error": None,
        "relationships_count": None,
        "pii_count": None,
    }
    with _jobs_lock:
        _jobs[job_id] = job

    # Persist immediately on submit so a crash before the runner starts
    # still leaves a queued row visible in the UI.
    _persist_job(job)

    # Clear stale orchestration state so the new job actually runs.  The
    # pipeline's run_log is scoped (phase, "global", None); without this
    # reset, a previous job's "succeeded" row makes _run_phase short-circuit
    # with `phase_already_complete_skipping` and the new job produces 0
    # results.  Also delete the prior analysis rows for THIS source schema
    # so tbl_inventory/col_inventory/etc. don't show duplicates after
    # re-extraction.  The persistent `jobs` table is intentionally NOT
    # touched here.
    _reset_pipeline_state_for_schema(req.schema_name)

    threading.Thread(target=_run_pipeline, args=(job_id,), daemon=True).start()
    return _job_status(job)


def _reset_pipeline_state_for_schema(schema_name: str) -> None:
    """Wipe orchestration audit (run_log) and prior analysis rows so a
    re-run produces fresh results.  Best-effort: swallow errors.

    The pipeline's ``run_log`` is keyed (phase, "global", None) and is NOT
    per-source-schema; without this reset, a previous job's "succeeded"
    row makes ``_run_phase`` short-circuit with
    ``phase_already_complete_skipping`` and the new job produces 0 results.

    The persistent ``jobs`` table is intentionally preserved — it carries
    UI history.  Concurrent jobs against the same backend are unsupported.
    """
    try:
        conn = psycopg2.connect(**RESULTS_DB_DSN)
    except Exception as exc:
        print(f"[reset_state_skipped] db_connect_failed error={exc}", flush=True)
        return
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # TRUNCATE every analysis table.  RESTART IDENTITY resets the
            # BIGSERIAL PKs so col_id / table_id stay in a fresh range.
            # CASCADE follows any FK chains between them.
            cur.execute("""
                TRUNCATE TABLE
                  discovery.run_log,
                  discovery.relationships,
                  discovery.composite_relationships,
                  discovery.polymorphic_relationships,
                  discovery.jsonb_relationships,
                  discovery.fk_candidates,
                  discovery.pii_leaks,
                  discovery.pii_findings,
                  discovery.clusters,
                  discovery.col_inventory,
                  discovery.tbl_inventory
                RESTART IDENTITY CASCADE
            """)
    except Exception as exc:
        # If a table is missing on an older DB schema, retry with a per-table
        # truncate so we still clear what exists.
        print(f"[reset_state_partial] schema={schema_name} primary_truncate_failed: {exc}",
              flush=True)
        try:
            conn.autocommit = True
            for tbl in (
                "run_log", "relationships", "composite_relationships",
                "polymorphic_relationships", "jsonb_relationships",
                "fk_candidates", "pii_leaks", "pii_findings", "clusters",
                "col_inventory", "tbl_inventory",
            ):
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"TRUNCATE TABLE discovery.{tbl} RESTART IDENTITY CASCADE"
                        )
                except Exception:
                    pass
        except Exception as exc2:
            print(f"[reset_state_failed] {exc2}", flush=True)
    finally:
        conn.close()


@app.get("/api/jobs", response_model=list[JobStatus])
def list_jobs() -> list[JobStatus]:
    with _jobs_lock:
        # Newest first
        return [
            _job_status(j) for j in sorted(
                _jobs.values(),
                key=lambda j: j["submitted_at"], reverse=True,
            )
        ]


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return _job_status(job)


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@app.get("/api/jobs/{job_id}/log")
def get_job_log(job_id: str, tail: int = 200) -> dict[str, str]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    log_path = job["log_path"]
    if not log_path.exists():
        return {"log": ""}
    with log_path.open() as f:
        lines = f.readlines()[-tail:]
    # The pipeline uses structlog + rich for coloured console output, so the
    # log file is full of ANSI escape sequences. Browsers render them as
    # invisible control characters, which makes the Run-log tab appear blank.
    # Strip them server-side so the UI shows plain text.
    return {"log": _ANSI_ESCAPE_RE.sub("", "".join(lines))}


@app.get("/api/jobs/{job_id}/run_log")
def get_job_run_log(job_id: str, detail: str = "rollup") -> dict[str, Any]:
    """Structured per-phase audit from the ``discovery.run_log`` table.

    The ``/log`` endpoint above returns the raw subprocess stdout/stderr;
    this returns the phase status timeline that the UI's Run-log tab
    renders.

    ``detail`` modes:

    * ``rollup`` (default): one row per ``phase`` summarising status
      across all scopes (global + table + column). Plus every failed
      sub-scope row so problems are still visible. ~14-30 rows for a
      typical job — what the UI table should display.
    * ``full``: every ``run_log`` row (can be thousands; one per column
      for fingerprint / pii_scan / validate). Used by deep-debug views.
    """
    if job_id not in _jobs:
        raise HTTPException(404, "job not found")
    conn = psycopg2.connect(**RESULTS_DB_DSN, options="-c search_path=discovery")
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT phase, scope_type, scope_id, status,
                   started_at, ended_at, error_message
            FROM run_log
            ORDER BY started_at ASC NULLS LAST, log_id ASC
            """
        )
        raw = cur.fetchall()
    finally:
        conn.close()

    def _row(r: tuple) -> dict[str, Any]:
        return {
            "phase": r[0],
            "scope_type": r[1],
            "scope_id": r[2],
            "status": r[3],
            "started_at": r[4].isoformat() if r[4] else None,
            "ended_at": r[5].isoformat() if r[5] else None,
            "error_message": r[6],
        }

    if detail == "full":
        return {"entries": [_row(r) for r in raw]}

    # Rollup mode: one summary row per phase plus every non-succeeded sub-scope.
    phase_groups: dict[str, list[tuple]] = {}
    for r in raw:
        phase_groups.setdefault(r[0], []).append(r)

    summary: list[dict[str, Any]] = []
    for phase, rows in phase_groups.items():
        # Prefer the global-scope row as the headline; otherwise aggregate.
        global_row = next((r for r in rows if r[1] == "global"), None)
        sub_total = sum(1 for r in rows if r[1] != "global")
        sub_failed = sum(1 for r in rows if r[1] != "global" and r[3] == "failed")
        if global_row is not None:
            entry = _row(global_row)
        else:
            # Synthesise a phase-level entry from the sub-scopes.
            started = min((r[4] for r in rows if r[4]), default=None)
            ended = max((r[5] for r in rows if r[5]), default=None)
            any_fail = any(r[3] == "failed" for r in rows)
            entry = {
                "phase": phase,
                "scope_type": "phase",
                "scope_id": 0,
                "status": "failed" if any_fail else "succeeded",
                "started_at": started.isoformat() if started else None,
                "ended_at": ended.isoformat() if ended else None,
                "error_message": None,
            }
        if sub_total:
            entry["sub_total"] = sub_total
            entry["sub_failed"] = sub_failed
        summary.append(entry)

    # Append every failed sub-scope row so the UI still surfaces failures.
    failures = [_row(r) for r in raw if r[1] != "global" and r[3] == "failed"]

    summary.sort(key=lambda e: e.get("started_at") or "")
    return {"entries": summary + failures}


_ROLE_SUFFIXES_API: frozenset[str] = frozenset({
    "manager_id", "parent_id", "head_id", "owner_id", "supervisor_id",
    "referrer_id", "reports_to", "head_employee_id", "head_user_id",
    "approved_by", "assigned_to", "created_by", "modified_by",
    "updated_by", "managed_by", "posted_by", "reported_by",
    "reviewer_id", "assigned_hr_id", "submitted_by", "received_by",
    "from_id", "to_id", "predecessor_id", "successor_id",
})


def _derive_direction_reason(
    *,
    child_col_name: str,
    parent_col_name: str,
    child_is_pk: bool,
    parent_is_pk: bool,
    parent_is_unique: bool,
) -> str:
    """One-line "why this direction?" reason for UI tooltips.

    Order of checks mirrors the Phase-4 reconciliation rules:
      1. role-FK bypass — child column matches a known role suffix and
         parent column is the PK ('id' or '<table>_id').
      2. declared PK on parent — vanilla FK case.
      3. inheritance — both PKs, larger parent (IS-A pattern).
      4. implicit PK reconciled — neither declared, but parent has
         ``is_unique_indexed`` set (the post-reconcile flag, the
         closest persisted approximation of ``is_implicit_pk``).
      5. fallback "name similarity / containment" — no other signal.
    """
    cc = (child_col_name or "").lower()
    pc = (parent_col_name or "").lower()

    # 1. Role-FK pattern: child column is a recognised role suffix
    # (manager_id, posted_by, ...) AND parent is a PK-shaped column
    # ('id' or '<table>_id').
    if cc in _ROLE_SUFFIXES_API and (pc == "id" or pc.endswith("_id")):
        return "role-FK bypass"

    # 2. Declared PK on parent (single-side).
    if parent_is_pk and not child_is_pk:
        return "declared PK on parent"

    # 3. Both declared PK — inheritance / IS-A.
    if parent_is_pk and child_is_pk:
        return "inheritance — both PKs, larger parent"

    # 4. Implicit PK / unique-indexed signal on parent.
    if parent_is_unique:
        return "implicit PK reconciled"

    # 5. Fallback.
    return "name similarity / containment"


@app.get("/api/jobs/{job_id}/relationships")
def get_relationships(job_id: str, limit: int = 500) -> dict[str, Any]:
    """Return a graph payload for the relationship-graph component.

    Sprint A8: each edge now carries:
      * ``evidence`` — the raw JSONB ``relationships.evidence`` map
        written by Phase 5 (orphan_count, child_distinct, parent_distinct,
        query_duration_ms, source_stage, sketch_similarity).
      * ``direction_reason`` — short string explaining why the
        candidate landed in the (child, parent) direction it did, for
        the UI tooltip (declared PK on parent / inheritance / implicit
        PK reconciled / role-FK bypass / name similarity / containment).
      * ``composite_columns`` — when non-null, the edge represents a
        composite (multi-column) FK folded in via the unified view.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    schema = job["schema_name"]

    conn = psycopg2.connect(**RESULTS_DB_DSN, options="-c search_path=discovery")
    try:
        with conn.cursor() as cur:
            # Tables for this schema
            cur.execute("""
                SELECT table_id, table_name FROM tbl_inventory
                WHERE schema_name = %s ORDER BY table_name
            """, (schema,))
            tables = cur.fetchall()
            table_by_id = {t[0]: t[1] for t in tables}

            # Sprint A8: query the unified view (relationships +
            # composite_relationships UNION) when it exists; fall back
            # to the relationships table directly otherwise.
            unified_view_exists = False
            try:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.views
                        WHERE table_schema = 'discovery'
                          AND table_name = 'relationships_unified'
                    )
                """)
                unified_view_exists = bool(cur.fetchone()[0])
            except Exception:
                unified_view_exists = False

            base_query = """
                SELECT ct.table_name, cc.column_name,
                       pt.table_name, pc.column_name,
                       r.containment_full, r.cardinality, r.confidence,
                       r.evidence,
                       cc.is_pk, pc.is_pk,
                       cc.is_unique_indexed, pc.is_unique_indexed,
                       cc.distinct_count, pc.distinct_count
                {extra_select}
                FROM {source} r
                JOIN col_inventory cc ON cc.column_id = r.child_col_id
                JOIN tbl_inventory ct ON ct.table_id = cc.table_id
                JOIN col_inventory pc ON pc.column_id = r.parent_col_id
                JOIN tbl_inventory pt ON pt.table_id = pc.table_id
                WHERE ct.schema_name = %s
                ORDER BY r.confidence DESC NULLS LAST
                LIMIT %s
            """

            if unified_view_exists:
                query = base_query.format(
                    source="relationships_unified",
                    extra_select=", r.composite_columns",
                )
            else:
                query = base_query.format(
                    source="relationships",
                    extra_select=", NULL::jsonb AS composite_columns",
                )

            cur.execute(query, (schema, limit))
            rels = cur.fetchall()
    finally:
        conn.close()

    # Build nodes (only tables that participate in at least one edge)
    table_degree: dict[str, int] = defaultdict(int)
    for ct, cc, pt, pc, *_ in rels:
        table_degree[ct] += 1
        table_degree[pt] += 1

    nodes = [
        {"id": name, "label": name, "value": deg}
        for name, deg in table_degree.items()
    ]

    edges: list[dict[str, Any]] = []
    for row in rels:
        (
            ct, cc_name, pt, pc_name,
            cont, card, conf, evidence,
            cc_is_pk, pc_is_pk,
            cc_is_unique, pc_is_unique,
            cc_distinct, pc_distinct,
            comp_cols,
        ) = row
        # Phase 4's ``is_implicit_pk`` is in-memory only; we approximate
        # via ``is_unique_indexed`` (the post-reconcile flag) inside
        # _derive_direction_reason.
        reason = _derive_direction_reason(
            child_col_name=cc_name,
            parent_col_name=pc_name,
            child_is_pk=bool(cc_is_pk),
            parent_is_pk=bool(pc_is_pk),
            parent_is_unique=bool(pc_is_unique),
        )
        edges.append({
            "from": ct, "to": pt,
            "label": f"{cc_name} → {pc_name}",
            "containment": float(cont) if cont is not None else None,
            "cardinality": card,
            "confidence": float(conf) if conf is not None else None,
            "evidence": evidence,
            "direction_reason": reason,
            "composite_columns": comp_cols,
        })
    return {
        "schema": schema,
        "nodes": nodes,
        "edges": edges,
        "total_edges": len(edges),
        "total_tables": len(tables),
    }


@app.get("/api/jobs/{job_id}/pii")
def get_pii_findings(job_id: str) -> dict[str, Any]:
    """Return PII findings table for the UI."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    schema = job["schema_name"]

    conn = psycopg2.connect(**RESULTS_DB_DSN, options="-c search_path=discovery")
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.table_name, c.column_name,
                       p.pii_type, p.detector,
                       p.match_count, p.sample_count, p.match_rate,
                       p.validated, COALESCE(p.name_prior, false) AS name_prior,
                       p.score, p.redacted_examples
                FROM pii_findings p
                JOIN col_inventory c ON c.column_id = p.column_id
                JOIN tbl_inventory t ON t.table_id = c.table_id
                WHERE t.schema_name = %s
                ORDER BY t.table_name, c.column_name, p.pii_type
            """, (schema,))
            rows = cur.fetchall()
    finally:
        conn.close()

    findings = [
        {
            "table_name": r[0], "column_name": r[1],
            "pii_type": r[2], "detector": r[3],
            "match_count": r[4], "sample_count": r[5],
            "match_rate": float(r[6]) if r[6] is not None else 0.0,
            "validated": r[7], "name_prior": r[8],
            "score": float(r[9]) if r[9] is not None else None,
            "redacted_examples": r[10] or [],
        }
        for r in rows
    ]
    return {"schema": schema, "findings": findings, "total": len(findings)}


# ---------------------------------------------------------- helpers

def _job_status(job: dict[str, Any]) -> JobStatus:
    return JobStatus(
        job_id=job["job_id"],
        label=job["label"],
        schema_name=job["schema_name"],
        status=job["status"],
        submitted_at=job["submitted_at"],
        started_at=job.get("started_at"),
        ended_at=job.get("ended_at"),
        current_phase=job.get("current_phase"),
        progress=job.get("progress", {}),
        error=job.get("error"),
        relationships_count=job.get("relationships_count"),
        pii_count=job.get("pii_count"),
        cluster_count=job.get("cluster_count"),
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# =====================================================================
# Cluster-engine sprint (CL-3). Additive endpoints — no existing routes
# modified except /summary (extended below in _summary_from_results_db).
# =====================================================================

def _clusters_table_exists(cur) -> bool:
    """Return True only when CL-2's clusters table is present."""
    try:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'discovery'
                  AND table_name = 'clusters'
            )
        """)
        return bool(cur.fetchone()[0])
    except Exception:
        return False


def _col_exists(cur, table: str, column: str) -> bool:
    """Return True when column exists on a discovery-schema table."""
    try:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'discovery'
                  AND table_name = %s
                  AND column_name = %s
            )
        """, (table, column))
        return bool(cur.fetchone()[0])
    except Exception:
        return False


@app.get("/api/jobs/{job_id}/clusters")
def get_clusters(job_id: str) -> dict[str, Any]:
    """Return cluster list for the schema attached to this job.

    Defensive: if the clusters table (CL-2) does not exist yet, returns an
    empty payload with HTTP 200 rather than 500.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    schema: str = job["schema_name"]

    conn = psycopg2.connect(**RESULTS_DB_DSN, options="-c search_path=discovery")
    try:
        with conn.cursor() as cur:
            if not _clusters_table_exists(cur):
                return {
                    "schema": schema,
                    "total_clusters": 0,
                    "modularity": 0.0,
                    "junctions_collapsed": 0,
                    "clusters": [],
                }

            # Top-level aggregates
            cur.execute("""
                SELECT count(*),
                       COALESCE(sum(modularity_score), 0.0)
                FROM clusters
                WHERE schema_name = %s
            """, (schema,))
            total_clusters, total_modularity = cur.fetchone()
            total_clusters = int(total_clusters or 0)
            total_modularity = float(total_modularity or 0.0)

            # junctions_collapsed: tbl_inventory rows with junction_collapsed=true
            junctions_collapsed = 0
            if _col_exists(cur, "tbl_inventory", "junction_collapsed"):
                cur.execute("""
                    SELECT count(*)
                    FROM tbl_inventory
                    WHERE schema_name = %s
                      AND junction_collapsed = true
                """, (schema,))
                junctions_collapsed = int(cur.fetchone()[0] or 0)

            # Fetch all clusters for schema
            cur.execute("""
                SELECT cluster_local_id,
                       name,
                       table_count,
                       intra_edge_count,
                       inter_edge_count,
                       archetype_distribution,
                       modularity_score,
                       member_table_ids
                FROM clusters
                WHERE schema_name = %s
                ORDER BY cluster_local_id
            """, (schema,))
            cluster_rows = cur.fetchall()

            # For pii_table_count and subject_kinds, we join tbl_inventory to
            # member_table_ids. subject_kinds column may be absent.
            has_subject_kinds = _col_exists(cur, "tbl_inventory", "subject_kinds")

            clusters_out: list[dict[str, Any]] = []
            for row in cluster_rows:
                (
                    c_local_id, c_name, c_table_count,
                    c_intra, c_inter, c_archetype_dist,
                    c_mod_score, c_member_ids,
                ) = row

                member_ids: list[int] = list(c_member_ids) if c_member_ids else []
                pii_table_count = 0
                subject_kinds_union: list[str] = []

                if member_ids:
                    if has_subject_kinds:
                        cur.execute("""
                            SELECT count(DISTINCT t.table_id),
                                   array_agg(DISTINCT sk)
                            FROM tbl_inventory t,
                                 jsonb_array_elements_text(
                                     CASE
                                         WHEN jsonb_typeof(t.subject_kinds) = 'array'
                                             THEN t.subject_kinds
                                         ELSE '[]'::jsonb
                                     END
                                 ) AS sk
                            WHERE t.table_id = ANY(%s)
                              AND EXISTS (
                                  SELECT 1 FROM pii_findings p
                                  JOIN col_inventory c ON c.column_id = p.column_id
                                  WHERE c.table_id = t.table_id
                              )
                        """, (member_ids,))
                        r = cur.fetchone()
                        pii_table_count = int(r[0] or 0)
                        raw_sk = r[1] or []
                        subject_kinds_union = [
                            s for s in raw_sk if s is not None
                        ]
                    else:
                        # subject_kinds column not yet present — just count PII tables
                        cur.execute("""
                            SELECT count(DISTINCT t.table_id)
                            FROM tbl_inventory t
                            WHERE t.table_id = ANY(%s)
                              AND EXISTS (
                                  SELECT 1 FROM pii_findings p
                                  JOIN col_inventory c ON c.column_id = p.column_id
                                  WHERE c.table_id = t.table_id
                              )
                        """, (member_ids,))
                        pii_table_count = int(cur.fetchone()[0] or 0)

                clusters_out.append({
                    "cluster_id": int(c_local_id),
                    "name": c_name or "",
                    "table_count": int(c_table_count or 0),
                    "intra_edges": int(c_intra or 0),
                    "inter_edges": int(c_inter or 0),
                    "archetype_distribution": dict(c_archetype_dist or {}),
                    "modularity_contribution": float(c_mod_score or 0.0),
                    "pii_table_count": pii_table_count,
                    "subject_kinds": sorted(set(subject_kinds_union)),
                })

            # --- Inter-cluster edges (for the macro cluster-graph view) ---
            # For every pair of distinct clusters, count the number of FK
            # relationships that span them.  Edges are emitted as undirected
            # (sorted pair) with a count and the cardinality mix.
            cluster_edges_out: list[dict[str, Any]] = []
            try:
                cur.execute("""
                    WITH tbl_to_cluster AS (
                        SELECT t.table_id, c.cluster_local_id
                        FROM clusters c,
                             jsonb_array_elements_text(c.member_table_ids) AS m
                        JOIN tbl_inventory t
                          ON t.table_id = m::bigint
                         AND t.schema_name = c.schema_name
                        WHERE c.schema_name = %s
                    )
                    SELECT LEAST(c1.cluster_local_id, c2.cluster_local_id) AS a,
                           GREATEST(c1.cluster_local_id, c2.cluster_local_id) AS b,
                           count(*) AS n
                    FROM relationships r
                    JOIN col_inventory cc ON cc.column_id = r.child_col_id
                    JOIN col_inventory pc ON pc.column_id = r.parent_col_id
                    JOIN tbl_to_cluster c1 ON c1.table_id = cc.table_id
                    JOIN tbl_to_cluster c2 ON c2.table_id = pc.table_id
                    WHERE c1.cluster_local_id <> c2.cluster_local_id
                    GROUP BY 1, 2
                    ORDER BY 1, 2
                """, (schema,))
                for er in cur.fetchall():
                    cluster_edges_out.append({
                        "from": int(er[0]),
                        "to":   int(er[1]),
                        "count": int(er[2]),
                    })
            except Exception:
                # Schema might be older / lacking columns. Don't block the
                # main payload — just return empty edges.
                conn.rollback()
                cluster_edges_out = []
    finally:
        conn.close()

    return {
        "schema": schema,
        "total_clusters": total_clusters,
        "modularity": round(total_modularity, 6),
        "junctions_collapsed": junctions_collapsed,
        "clusters": clusters_out,
        "cluster_edges": cluster_edges_out,   # NEW: macro cluster-graph
    }


@app.get("/api/jobs/{job_id}/clusters/{cluster_local_id}")
def get_cluster_detail(job_id: str, cluster_local_id: int) -> dict[str, Any]:
    """Return detail for one cluster (tables, intra-cluster edges, PII findings).

    Defensive: if the clusters table does not exist, returns 404 with a
    descriptive message rather than 500.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    schema: str = job["schema_name"]

    conn = psycopg2.connect(**RESULTS_DB_DSN, options="-c search_path=discovery")
    try:
        with conn.cursor() as cur:
            if not _clusters_table_exists(cur):
                raise HTTPException(
                    404, "clusters table not yet available for this database"
                )

            # Fetch the specific cluster
            cur.execute("""
                SELECT cluster_local_id,
                       name,
                       member_table_ids
                FROM clusters
                WHERE schema_name = %s
                  AND cluster_local_id = %s
            """, (schema, cluster_local_id))
            cluster_row = cur.fetchone()
            if not cluster_row:
                raise HTTPException(404, f"cluster {cluster_local_id} not found")

            c_local_id, c_name, c_member_ids = cluster_row
            member_ids: list[int] = list(c_member_ids) if c_member_ids else []

            # --- Member tables ---
            has_archetype = _col_exists(cur, "tbl_inventory", "archetype")
            has_subject_kinds = _col_exists(cur, "tbl_inventory", "subject_kinds")

            if member_ids:
                select_parts = [
                    "t.table_id",
                    "t.table_name",
                    "COALESCE(t.row_count_estimate, 0) AS row_count",
                ]
                if has_archetype:
                    select_parts.append("t.archetype")
                else:
                    select_parts.append("NULL::text AS archetype")
                if has_subject_kinds:
                    select_parts.append("t.subject_kinds")
                else:
                    select_parts.append("NULL::jsonb AS subject_kinds")

                cur.execute(
                    "SELECT {} FROM tbl_inventory t WHERE t.table_id = ANY(%s)"
                    " ORDER BY t.table_name".format(", ".join(select_parts)),
                    (member_ids,),
                )
                table_rows = cur.fetchall()
            else:
                table_rows = []

            tables_out: list[dict[str, Any]] = []
            table_name_by_id: dict[int, str] = {}
            for tr in table_rows:
                t_id, t_name, t_rows, t_archetype, t_sk = tr
                table_name_by_id[int(t_id)] = t_name

                # Normalise subject_kinds: could be a list (JSONB decoded),
                # a pg text[] proxy, or None.
                if t_sk is None:
                    sk_list = None
                elif isinstance(t_sk, list):
                    sk_list = t_sk
                else:
                    # psycopg2 returns JSONB as dict/list; text[] as list too.
                    # Coerce anything unexpected to None.
                    try:
                        import json as _json
                        sk_list = _json.loads(t_sk) if isinstance(t_sk, str) else list(t_sk)
                    except Exception:
                        sk_list = None

                tables_out.append({
                    "table_id": int(t_id),
                    "table_name": t_name,
                    "row_count": int(t_rows or 0),
                    "archetype": t_archetype or "",
                    "subject_kinds": sk_list,
                })

            # --- Intra-cluster edges (both endpoints in member_ids) ---
            edges_out: list[dict[str, Any]] = []
            if member_ids:
                cur.execute("""
                    SELECT ct.table_name, cc.column_name,
                           pt.table_name, pc.column_name,
                           r.confidence, r.cardinality
                    FROM relationships r
                    JOIN col_inventory cc ON cc.column_id = r.child_col_id
                    JOIN tbl_inventory ct ON ct.table_id = cc.table_id
                    JOIN col_inventory pc ON pc.column_id = r.parent_col_id
                    JOIN tbl_inventory pt ON pt.table_id = pc.table_id
                    WHERE ct.table_id = ANY(%s)
                      AND pt.table_id = ANY(%s)
                    ORDER BY r.confidence DESC NULLS LAST
                """, (member_ids, member_ids))
                for er in cur.fetchall():
                    edges_out.append({
                        "from": er[0],
                        "to": er[2],
                        "child_column": er[1],
                        "parent_column": er[3],
                        "confidence": float(er[4]) if er[4] is not None else None,
                        "cardinality": er[5] or "",
                    })

            # --- PII findings for member tables ---
            pii_out: list[dict[str, Any]] = []
            if member_ids:
                cur.execute("""
                    SELECT t.table_name, c.column_name,
                           p.pii_type, p.score, p.validated
                    FROM pii_findings p
                    JOIN col_inventory c ON c.column_id = p.column_id
                    JOIN tbl_inventory t ON t.table_id = c.table_id
                    WHERE t.table_id = ANY(%s)
                    ORDER BY t.table_name, c.column_name, p.pii_type
                """, (member_ids,))
                for pr in cur.fetchall():
                    pii_out.append({
                        "table_name": pr[0],
                        "column_name": pr[1],
                        "pii_type": pr[2],
                        "score": float(pr[3]) if pr[3] is not None else None,
                        "validated": bool(pr[4]),
                    })

            # --- Cross-cluster "bridge" tables (super-points) ---
            # Tables OUTSIDE this cluster that have an FK edge to/from a
            # member table.  These are the join points we surface in the
            # cluster ERD as outline-only ghost cards.
            cross_edges_out: list[dict[str, Any]] = []
            bridges: dict[str, dict[str, Any]] = {}
            if member_ids:
                cur.execute("""
                    SELECT ct.table_name, cc.column_name,
                           pt.table_name, pc.column_name,
                           r.confidence, r.cardinality,
                           ct.table_id, pt.table_id
                    FROM relationships r
                    JOIN col_inventory cc ON cc.column_id = r.child_col_id
                    JOIN tbl_inventory ct ON ct.table_id = cc.table_id
                    JOIN col_inventory pc ON pc.column_id = r.parent_col_id
                    JOIN tbl_inventory pt ON pt.table_id = pc.table_id
                    WHERE (ct.table_id = ANY(%s) OR pt.table_id = ANY(%s))
                      AND NOT (ct.table_id = ANY(%s) AND pt.table_id = ANY(%s))
                """, (member_ids, member_ids, member_ids, member_ids))
                cross_rows = cur.fetchall()

                # Map outside-table_id → its cluster (name + local_id) so the
                # UI can label the bridge with "→ <other_cluster>".
                outside_ids = set()
                for cr in cross_rows:
                    if cr[6] not in member_ids: outside_ids.add(int(cr[6]))
                    if cr[7] not in member_ids: outside_ids.add(int(cr[7]))
                cluster_lookup: dict[int, dict[str, Any]] = {}
                if outside_ids:
                    cur.execute("""
                        SELECT t.table_id, t.table_name, c.cluster_local_id, c.name
                        FROM tbl_inventory t
                        LEFT JOIN clusters c
                          ON c.schema_name = t.schema_name
                         AND t.table_id = ANY(
                               SELECT jsonb_array_elements_text(c.member_table_ids)::bigint
                             )
                        WHERE t.table_id = ANY(%s)
                    """, (list(outside_ids),))
                    for lr in cur.fetchall():
                        cluster_lookup[int(lr[0])] = {
                            "table_name": lr[1],
                            "cluster_id": int(lr[2]) if lr[2] is not None else None,
                            "cluster_name": lr[3] or "",
                        }

                for cr in cross_rows:
                    cross_edges_out.append({
                        "from": cr[0],
                        "to": cr[2],
                        "child_column": cr[1],
                        "parent_column": cr[3],
                        "confidence": float(cr[4]) if cr[4] is not None else None,
                        "cardinality": cr[5] or "",
                    })
                    # Identify the OUTSIDE endpoint
                    for tid in (cr[6], cr[7]):
                        if int(tid) not in member_ids:
                            info = cluster_lookup.get(int(tid))
                            if info and info["table_name"] not in bridges:
                                bridges[info["table_name"]] = {
                                    "table_name": info["table_name"],
                                    "to_cluster_id":   info["cluster_id"],
                                    "to_cluster_name": info["cluster_name"],
                                }
    finally:
        conn.close()

    return {
        "cluster_id": int(c_local_id),
        "name": c_name or "",
        "tables": tables_out,
        "edges": edges_out,
        "pii_findings": pii_out,
        # NEW: cross-cluster join points
        "bridge_tables":       sorted(bridges.values(), key=lambda b: b["table_name"]),
        "cross_cluster_edges": cross_edges_out,
    }


# =====================================================================
# UI-4 additions (additive only). These endpoints power the cross-schema
# dashboard component. They do NOT modify any existing endpoint above.
# =====================================================================

# Source-DB DSN used by /api/schemas to enumerate seeded schemas in the
# `test` database. UI-1 may parameterise this later — kept literal for now
# to match the four currently-seeded fixtures (adv, public2, hr, dvdrental).
SOURCE_DB_DSN = dict(
    host=os.environ.get("SOURCE_DB_HOST", "localhost"),
    port=int(os.environ.get("SOURCE_DB_PORT", "5432")),
    dbname=os.environ.get("SOURCE_DB_NAME", "test"),
    user=os.environ.get("SOURCE_DB_USER", "adsuser"),
    # SOURCE_DB_PASSWORD must be set in the uvicorn env (see startup check).
    password=os.environ.get("SOURCE_DB_PASSWORD", ""),
)

# Hand-curated expected foreign-key sets, lifted from
# /tmp/check_<schema>_recall.py. When a job's schema_name is present here,
# the /api/jobs/{job_id}/summary response includes recall + precision.
EXPECTED_FKS: dict[str, list[tuple[str, str, str, str]]] = {
    "adv": [
        ("state_province", "country_region_code", "country_region", "country_region_code"),
        ("address", "state_province_id", "state_province", "state_province_id"),
        ("business_entity_address", "business_entity_id", "business_entity", "business_entity_id"),
        ("business_entity_address", "address_id", "address", "address_id"),
        ("business_entity_address", "address_type_id", "address_type", "address_type_id"),
        ("person", "business_entity_id", "business_entity", "business_entity_id"),
        ("email_address", "business_entity_id", "person", "business_entity_id"),
        ("person_phone", "business_entity_id", "person", "business_entity_id"),
        ("person_phone", "phone_number_type_id", "phone_number_type", "phone_number_type_id"),
        ("password", "business_entity_id", "person", "business_entity_id"),
        ("employee", "business_entity_id", "person", "business_entity_id"),
        ("employee_department_history", "business_entity_id", "employee", "business_entity_id"),
        ("employee_department_history", "department_id", "department", "department_id"),
        ("employee_department_history", "shift_id", "shift", "shift_id"),
        ("employee_pay_history", "business_entity_id", "employee", "business_entity_id"),
        ("product_subcategory", "product_category_id", "product_category", "product_category_id"),
        ("product", "product_subcategory_id", "product_subcategory", "product_subcategory_id"),
        ("product", "product_model_id", "product_model", "product_model_id"),
        ("product_inventory", "product_id", "product", "product_id"),
        ("product_inventory", "location_id", "location", "location_id"),
        ("product_review", "product_id", "product", "product_id"),
        ("product_cost_history", "product_id", "product", "product_id"),
        ("vendor", "business_entity_id", "business_entity", "business_entity_id"),
        ("product_vendor", "product_id", "product", "product_id"),
        ("product_vendor", "business_entity_id", "vendor", "business_entity_id"),
        ("purchase_order_header", "employee_id", "employee", "business_entity_id"),
        ("purchase_order_header", "vendor_id", "vendor", "business_entity_id"),
        ("purchase_order_header", "ship_method_id", "ship_method", "ship_method_id"),
        ("purchase_order_detail", "purchase_order_id", "purchase_order_header", "purchase_order_id"),
        ("purchase_order_detail", "product_id", "product", "product_id"),
        ("sales_territory", "country_region_code", "country_region", "country_region_code"),
        ("sales_person", "business_entity_id", "employee", "business_entity_id"),
        ("sales_person", "territory_id", "sales_territory", "territory_id"),
        ("store", "business_entity_id", "business_entity", "business_entity_id"),
        ("store", "sales_person_id", "sales_person", "business_entity_id"),
        ("customer", "person_id", "person", "business_entity_id"),
        ("customer", "store_id", "store", "business_entity_id"),
        ("customer", "territory_id", "sales_territory", "territory_id"),
        ("person_credit_card", "business_entity_id", "person", "business_entity_id"),
        ("person_credit_card", "credit_card_id", "credit_card", "credit_card_id"),
        ("special_offer_product", "special_offer_id", "special_offer", "special_offer_id"),
        ("special_offer_product", "product_id", "product", "product_id"),
        ("sales_order_header", "customer_id", "customer", "customer_id"),
        ("sales_order_header", "sales_person_id", "sales_person", "business_entity_id"),
        ("sales_order_header", "territory_id", "sales_territory", "territory_id"),
        ("sales_order_header", "bill_to_address_id", "address", "address_id"),
        ("sales_order_header", "ship_to_address_id", "address", "address_id"),
        ("sales_order_header", "ship_method_id", "ship_method", "ship_method_id"),
        ("sales_order_header", "credit_card_id", "credit_card", "credit_card_id"),
        ("sales_order_detail", "sales_order_id", "sales_order_header", "sales_order_id"),
        ("sales_order_detail", "product_id", "product", "product_id"),
        ("sales_order_detail", "special_offer_id", "special_offer", "special_offer_id"),
        ("sales_order_header_sales_reason", "sales_order_id", "sales_order_header", "sales_order_id"),
        ("sales_order_header_sales_reason", "sales_reason_id", "sales_reason", "sales_reason_id"),
    ],
    "public2": [
        ("orders", "customer_id", "customers", "id"),
        ("orders", "shipping_address_id", "addresses", "id"),
        ("addresses", "customer_id", "customers", "id"),
        ("order_items", "order_id", "orders", "id"),
        ("order_items", "product_id", "products", "id"),
        ("payments", "order_id", "orders", "id"),
        ("payments", "customer_id", "customers", "id"),
        ("inventory", "product_id", "products", "id"),
        ("warehouse_stock", "product_id", "products", "id"),
        ("warehouse_stock", "warehouse_id", "warehouses", "id"),
        ("users", "customer_id", "customers", "id"),
        ("user_roles", "user_id", "users", "id"),
        ("user_roles", "role_id", "roles", "id"),
        ("user_sessions", "user_id", "users", "id"),
        ("api_tokens", "user_id", "users", "id"),
        ("departments", "head_employee_id", "employee_records", "id"),
        ("tickets", "customer_id", "customers", "id"),
        ("tickets", "assigned_to", "employee_records", "id"),
        ("ticket_messages", "ticket_id", "tickets", "id"),
        ("ticket_messages", "author_user_id", "users", "id"),
        ("reviews", "product_id", "products", "id"),
        ("reviews", "customer_id", "customers", "id"),
        ("employee_records", "manager_id", "employee_records", "id"),
        ("categories", "parent_category_id", "categories", "id"),
        ("products", "category_id", "categories", "id"),
    ],
    "hr": [
        ("countries", "region_id", "regions", "id"),
        ("locations", "country_code", "countries", "code"),
        ("departments", "location_id", "locations", "id"),
        ("departments", "parent_department_id", "departments", "id"),
        ("cost_centers", "parent_cost_center_id", "cost_centers", "id"),
        ("jobs", "pay_grade_id", "pay_grades", "id"),
        ("skills", "taxonomy_id", "skill_taxonomy", "id"),
        ("skill_taxonomy", "parent_id", "skill_taxonomy", "id"),
        ("public_holidays", "country_code", "countries", "code"),
        ("employees", "department_id", "departments", "id"),
        ("employees", "location_id", "locations", "id"),
        ("employees", "manager_id", "employees", "id"),
        ("employees", "job_id", "jobs", "id"),
        ("employees", "pay_grade_id", "pay_grades", "id"),
        ("employees", "cost_center_id", "cost_centers", "id"),
        ("employees", "employment_type_id", "employment_types", "id"),
        ("job_history", "employee_id", "employees", "id"),
        ("job_history", "job_id", "jobs", "id"),
        ("job_history", "department_id", "departments", "id"),
        ("candidates", "referrer_id", "employees", "id"),
        ("job_postings", "job_id", "jobs", "id"),
        ("job_postings", "posted_by", "employees", "id"),
        ("applications", "candidate_id", "candidates", "id"),
        ("applications", "posting_id", "job_postings", "id"),
        ("interviews", "application_id", "applications", "id"),
        ("interviews", "interviewer_id", "employees", "id"),
        ("interview_feedback", "interview_id", "interviews", "id"),
        ("offers", "application_id", "applications", "id"),
        ("offers", "signed_by_candidate_id", "candidates", "id"),
        ("background_checks", "candidate_id", "candidates", "id"),
        ("payroll_entries", "employee_id", "employees", "id"),
        ("payroll_entries", "payroll_run_id", "payroll_runs", "id"),
        ("salaries", "employee_id", "employees", "id"),
        ("salaries", "pay_component_id", "pay_components", "id"),
        ("compensation_changes", "employee_id", "employees", "id"),
        ("compensation_changes", "approved_by", "employees", "id"),
        ("bonuses", "employee_id", "employees", "id"),
        ("timesheets", "employee_id", "employees", "id"),
        ("time_entries", "timesheet_id", "timesheets", "id"),
        ("time_entries", "employee_id", "employees", "id"),
        ("leave_requests", "employee_id", "employees", "id"),
        ("leave_requests", "leave_type_id", "leave_types", "id"),
        ("leave_requests", "approved_by", "employees", "id"),
        ("leave_balances", "employee_id", "employees", "id"),
        ("leave_balances", "leave_type_id", "leave_types", "id"),
        ("shift_assignments", "shift_id", "shifts", "id"),
        ("shift_assignments", "employee_id", "employees", "id"),
        ("performance_reviews", "employee_id", "employees", "id"),
        ("performance_reviews", "reviewer_id", "employees", "id"),
        ("performance_reviews", "cycle_id", "review_cycles", "id"),
        ("goals", "employee_id", "employees", "id"),
        ("goals", "review_id", "performance_reviews", "id"),
        ("competency_assessments", "employee_id", "employees", "id"),
        ("competency_assessments", "competency_id", "competencies", "id"),
        ("promotion_history", "employee_id", "employees", "id"),
        ("promotion_history", "from_job_id", "jobs", "id"),
        ("promotion_history", "to_job_id", "jobs", "id"),
        ("training_enrollments", "employee_id", "employees", "id"),
        ("training_enrollments", "program_id", "training_programs", "id"),
        ("certification_holders", "employee_id", "employees", "id"),
        ("certification_holders", "certification_id", "certifications", "id"),
        ("benefit_enrollments", "employee_id", "employees", "id"),
        ("benefit_enrollments", "plan_id", "benefit_plans", "id"),
        ("dependents", "employee_id", "employees", "id"),
        ("emergency_contacts", "employee_id", "employees", "id"),
        ("onboarding_progress", "employee_id", "employees", "id"),
        ("onboarding_progress", "task_id", "onboarding_tasks", "id"),
        ("exit_interviews", "employee_id", "employees", "id"),
        ("termination_records", "employee_id", "employees", "id"),
        ("termination_records", "approved_by", "employees", "id"),
        ("documents", "owner_employee_id", "employees", "id"),
        ("documents", "document_type_id", "document_types", "id"),
        ("document_acknowledgements", "document_id", "documents", "id"),
        ("document_acknowledgements", "employee_id", "employees", "id"),
        ("visa_statuses", "employee_id", "employees", "id"),
        ("incidents", "employee_id", "employees", "id"),
        ("incidents", "reported_by", "employees", "id"),
        ("disciplinary_actions", "incident_id", "incidents", "id"),
        ("disciplinary_actions", "employee_id", "employees", "id"),
        ("grievances", "employee_id", "employees", "id"),
        ("grievances", "assigned_hr_id", "employees", "id"),
        ("employee_skills", "employee_id", "employees", "id"),
        ("employee_skills", "skill_id", "skills", "id"),
        ("hr_tickets", "employee_id", "employees", "id"),
        ("hr_tickets", "assigned_hr_id", "employees", "id"),
        ("hr_ticket_messages", "ticket_id", "hr_tickets", "id"),
        ("hr_ticket_messages", "author_employee_id", "employees", "id"),
        ("announcements", "posted_by", "employees", "id"),
    ],
    "dvdrental": [
        ("city", "country_id", "country", "country_id"),
        ("address", "city_id", "city", "city_id"),
        ("customer", "store_id", "store", "store_id"),
        ("customer", "address_id", "address", "address_id"),
        ("staff", "address_id", "address", "address_id"),
        ("staff", "store_id", "store", "store_id"),
        ("store", "manager_staff_id", "staff", "staff_id"),
        ("store", "address_id", "address", "address_id"),
        ("film", "language_id", "language", "language_id"),
        ("film", "original_language_id", "language", "language_id"),
        ("film_actor", "actor_id", "actor", "actor_id"),
        ("film_actor", "film_id", "film", "film_id"),
        ("film_category", "film_id", "film", "film_id"),
        ("film_category", "category_id", "category", "category_id"),
        ("inventory", "film_id", "film", "film_id"),
        ("inventory", "store_id", "store", "store_id"),
        ("rental", "inventory_id", "inventory", "inventory_id"),
        ("rental", "customer_id", "customer", "customer_id"),
        ("rental", "staff_id", "staff", "staff_id"),
        ("payment", "customer_id", "customer", "customer_id"),
        ("payment", "staff_id", "staff", "staff_id"),
        ("payment", "rental_id", "rental", "rental_id"),
    ],
    "saleor": [
        ("account_address", "user_id", "account_user", "id"),
        ("product_category", "parent_id", "product_category", "id"),
        ("product_product", "category_id", "product_category", "id"),
        ("product_product", "product_type_id", "product_producttype", "id"),
        ("product_product", "default_variant_id", "product_productvariant", "id"),
        ("product_productvariant", "product_id", "product_product", "id"),
        ("product_collectionproduct", "collection_id", "product_collection", "id"),
        ("product_collectionproduct", "product_id", "product_product", "id"),
        ("attribute_assignedproductattribute", "product_id", "product_product", "id"),
        ("attribute_assignedproductattribute", "attribute_id", "attribute_attribute", "id"),
        ("attribute_assignedvariantattribute", "variant_id", "product_productvariant", "id"),
        ("attribute_assignedvariantattribute", "attribute_id", "attribute_attribute", "id"),
        ("warehouse_stock", "product_variant_id", "product_productvariant", "id"),
        ("warehouse_stock", "warehouse_id", "warehouse_warehouse", "id"),
        ("order_order", "user_id", "account_user", "id"),
        ("order_order", "channel_id", "channel_channel", "id"),
        ("order_order", "billing_address_id", "account_address", "id"),
        ("order_order", "shipping_address_id", "account_address", "id"),
        ("order_order", "voucher_id", "discount_voucher", "id"),
        ("order_orderline", "order_id", "order_order", "id"),
        ("order_orderline", "variant_id", "product_productvariant", "id"),
        ("order_fulfillment", "order_id", "order_order", "id"),
        ("order_fulfillment", "warehouse_id", "warehouse_warehouse", "id"),
        ("order_fulfillmentline", "fulfillment_id", "order_fulfillment", "id"),
        ("order_fulfillmentline", "order_line_id", "order_orderline", "id"),
        ("payment_payment", "checkout_id", "checkout_checkout", "token"),
        ("payment_payment", "order_id", "order_order", "id"),
        ("payment_transaction", "payment_id", "payment_payment", "id"),
        ("checkout_checkout", "user_id", "account_user", "id"),
        ("checkout_checkout", "channel_id", "channel_channel", "id"),
        ("checkout_checkout", "billing_address_id", "account_address", "id"),
        ("checkout_checkout", "shipping_address_id", "account_address", "id"),
        ("checkout_checkoutline", "checkout_id", "checkout_checkout", "token"),
        ("checkout_checkoutline", "variant_id", "product_productvariant", "id"),
        ("discount_promotionrule", "promotion_id", "discount_promotion", "id"),
        ("giftcard_giftcard", "created_by_id", "account_user", "id"),
        ("giftcard_giftcard", "used_by_id", "account_user", "id"),
        ("shipping_shippingmethod", "shipping_zone_id", "shipping_shippingzone", "id"),
        ("menu_menuitem", "menu_id", "menu_menu", "id"),
        ("menu_menuitem", "parent_id", "menu_menuitem", "id"),
        ("menu_menuitem", "category_id", "product_category", "id"),
        ("menu_menuitem", "collection_id", "product_collection", "id"),
        ("page_page", "page_type_id", "page_pagetype", "id"),
    ],
}


def _summary_from_results_db(schema_name: str) -> dict[str, Any]:
    """Pull table/row/relationship/PII counts and discovered FK set."""
    conn = psycopg2.connect(**RESULTS_DB_DSN, options="-c search_path=discovery")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*),
                       COALESCE(sum(row_count_estimate), 0)
                FROM tbl_inventory
                WHERE schema_name = %s
                """,
                (schema_name,),
            )
            tables, rows_total = cur.fetchone()

            cur.execute(
                """
                SELECT count(*) FROM relationships r
                JOIN col_inventory cc ON cc.column_id = r.child_col_id
                JOIN tbl_inventory ct ON ct.table_id = cc.table_id
                WHERE ct.schema_name = %s
                """,
                (schema_name,),
            )
            rel_count = cur.fetchone()[0]

            cur.execute(
                """
                SELECT count(*) FROM pii_findings p
                JOIN col_inventory c ON c.column_id = p.column_id
                JOIN tbl_inventory t ON t.table_id = c.table_id
                WHERE t.schema_name = %s
                """,
                (schema_name,),
            )
            pii_count = cur.fetchone()[0]

            cur.execute(
                """
                SELECT ct.table_name, cc.column_name,
                       pt.table_name, pc.column_name
                FROM relationships r
                JOIN col_inventory cc ON cc.column_id = r.child_col_id
                JOIN tbl_inventory ct ON ct.table_id = cc.table_id
                JOIN col_inventory pc ON pc.column_id = r.parent_col_id
                JOIN tbl_inventory pt ON pt.table_id = pc.table_id
                WHERE ct.schema_name = %s
                """,
                (schema_name,),
            )
            disc_fks = {(r[0], r[1], r[2], r[3]) for r in cur.fetchall()}
    finally:
        conn.close()
    return {
        "tables": int(tables or 0),
        "rows_total": int(rows_total or 0),
        "relationships_count": int(rel_count or 0),
        "pii_findings_count": int(pii_count or 0),
        "discovered_fks": disc_fks,
    }


def _cluster_summary_from_db(schema_name: str) -> dict[str, Any]:
    """Return cluster_count and top-3 clusters_by_size for /summary.

    Fully defensive: if the clusters table or necessary columns don't exist,
    returns zeros/empty list so the existing summary payload is unaffected.
    """
    try:
        conn = psycopg2.connect(
            **RESULTS_DB_DSN, options="-c search_path=discovery"
        )
        try:
            with conn.cursor() as cur:
                # Check existence before querying to avoid ugly exceptions.
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'discovery'
                          AND table_name = 'clusters'
                    )
                """)
                if not cur.fetchone()[0]:
                    return {"cluster_count": 0, "clusters_by_size": []}

                cur.execute("""
                    SELECT count(*)
                    FROM clusters
                    WHERE schema_name = %s
                """, (schema_name,))
                cluster_count = int(cur.fetchone()[0] or 0)

                cur.execute("""
                    SELECT cluster_local_id, name, table_count
                    FROM clusters
                    WHERE schema_name = %s
                    ORDER BY table_count DESC NULLS LAST
                    LIMIT 3
                """, (schema_name,))
                clusters_by_size = [
                    {
                        "cluster_id": int(r[0]),
                        "name": r[1] or "",
                        "table_count": int(r[2] or 0),
                    }
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()
        return {"cluster_count": cluster_count, "clusters_by_size": clusters_by_size}
    except Exception:
        return {"cluster_count": 0, "clusters_by_size": []}


def _phases_from_run_log(
    started_at: Optional[datetime], ended_at: Optional[datetime]
) -> list[str]:
    """Distinct succeeded phases that fall within the job's window.

    run_log has no job_id column, so we bound by [started_at, ended_at]. If
    the job is still running, we cap at "now". Returned in first-seen order.
    """
    if started_at is None:
        return []
    end_cap = ended_at or _now()
    try:
        conn = psycopg2.connect(
            **RESULTS_DB_DSN, options="-c search_path=discovery"
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT phase, min(started_at) AS first_seen
                    FROM run_log
                    WHERE status = 'succeeded'
                      AND started_at >= %s
                      AND ended_at   <= %s
                    GROUP BY phase
                    ORDER BY first_seen
                    """,
                    (started_at, end_cap),
                )
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


@app.get("/api/jobs/{job_id}/summary")
def get_job_summary(job_id: str) -> dict[str, Any]:
    """Aggregate snapshot of one job for the dashboard.

    Includes recall + precision when the schema has expected FKs configured.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")

    schema = job["schema_name"]
    started_at = job.get("started_at")
    ended_at = job.get("ended_at")

    duration_seconds: Optional[float] = None
    if started_at and ended_at:
        duration_seconds = round(
            (ended_at - started_at).total_seconds(), 2
        )

    try:
        stats = _summary_from_results_db(schema)
    except Exception as exc:
        raise HTTPException(500, f"results db query failed: {exc}")

    phases = _phases_from_run_log(started_at, ended_at)

    payload: dict[str, Any] = {
        "job_id": job_id,
        "schema_name": schema,
        "tables": stats["tables"],
        "rows_total": stats["rows_total"],
        "relationships_count": stats["relationships_count"],
        "pii_findings_count": stats["pii_findings_count"],
        "duration_seconds": duration_seconds,
        "phase_complete": phases,
    }

    expected = EXPECTED_FKS.get(schema)
    if expected:
        disc = stats["discovered_fks"]
        expected_set = {tuple(fk) for fk in expected}
        matched = expected_set & disc
        n_expected = len(expected_set)
        n_disc = len(disc)
        payload["expected_fks"] = n_expected
        payload["matched_fks"] = len(matched)
        payload["recall"] = round(len(matched) / n_expected, 4) if n_expected else None
        payload["precision"] = round(len(matched) / n_disc, 4) if n_disc else None

    # Cluster-engine sprint (CL-3): append cluster summary fields.
    # Fully defensive — missing clusters table returns zeros, never raises.
    cluster_info = _cluster_summary_from_db(schema)
    payload["cluster_count"] = cluster_info["cluster_count"]
    payload["clusters_by_size"] = cluster_info["clusters_by_size"]

    return payload


@app.get("/api/schemas")
def list_source_schemas() -> dict[str, Any]:
    """Enumerate non-system schemas in the source `test` database.

    Used by the dashboard to populate the schema dropdown for "Run all".
    """
    try:
        conn = psycopg2.connect(**SOURCE_DB_DSN)
    except Exception as exc:
        raise HTTPException(503, f"source db unreachable: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, count(*) AS table_count
                FROM information_schema.tables
                WHERE table_schema NOT IN (
                    'pg_catalog', 'information_schema', 'public'
                )
                GROUP BY table_schema
                ORDER BY table_schema
                """
            )
            schemas = [
                {"schema_name": r[0], "table_count": int(r[1])}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()
    return {
        "source": {
            "host": SOURCE_DB_DSN["host"],
            "port": SOURCE_DB_DSN["port"],
            "database": SOURCE_DB_DSN["dbname"],
            "user": SOURCE_DB_DSN["user"],
        },
        "schemas": schemas,
        "total": len(schemas),
    }


# ---------------------------------------------------------- ERD card view (B2)
#
# Column-level inventory for the dbdiagram.io-style ERD card component.
# Lists every column for every table in the job's schema, plus PK/FK flags.
# is_fk is derived from membership in relationships.child_col_id (col_inventory
# itself only carries is_fk_eligible, which is a pre-discovery hint).
# Auth-free GET to match the other read endpoints (only POST /api/jobs is gated).
@app.get("/api/jobs/{job_id}/columns")
def get_job_columns(job_id: str) -> dict[str, Any]:
    """Return column-level inventory for the schema attached to this job.

    Used by the ERD card view (erd-card.component.ts) to render columns inside
    each table card with PK/FK badges. Returns ALL tables in the schema, not
    just those participating in discovered relationships.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    schema = job["schema_name"]

    conn = psycopg2.connect(**RESULTS_DB_DSN, options="-c search_path=discovery")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    t.table_name,
                    c.column_name,
                    c.ordinal_position,
                    c.data_type,
                    c.is_pk,
                    EXISTS (
                        SELECT 1 FROM relationships r
                        WHERE r.child_col_id = c.column_id
                    ) AS is_fk
                FROM tbl_inventory t
                JOIN col_inventory c ON c.table_id = t.table_id
                WHERE t.schema_name = %s
                ORDER BY t.table_name, c.ordinal_position
                """,
                (schema,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    columns = [
        {
            "table": r[0],
            "column": r[1],
            "ordinal": int(r[2]),
            "data_type": r[3],
            "is_pk": bool(r[4]),
            "is_fk": bool(r[5]),
        }
        for r in rows
    ]
    # tables array enumerates every table in the schema, even with zero columns
    # (shouldn't happen, but defensive). The ERD view renders one card per table.
    table_names = sorted({c["table"] for c in columns})
    return {
        "schema": schema,
        "tables": table_names,
        "columns": columns,
        "total_columns": len(columns),
        "total_tables": len(table_names),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
