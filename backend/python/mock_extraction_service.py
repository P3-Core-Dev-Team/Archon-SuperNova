"""
Lightweight Python mock of the Spring Boot extraction service.

Purpose: enable end-to-end testing of the Python discovery pipeline against
a real Postgres source when the Java toolchain (Maven, JDK 21) isn't
available. Implements the OpenAPI 1.1.1 contract:

  POST /api/v1/extract            -> sync extraction, writes Parquet, returns manifest
  POST /api/v1/connections/test   -> opens + closes a connection, returns 200/400
  GET  /actuator/health           -> always 200

Uses stdlib http.server (no Flask/FastAPI dep). Does NOT replicate JSqlParser
whitelist semantics — it accepts the catalog reads the inventory phase issues
and rejects obvious bad patterns. The full whitelist enforcement is what the
Spring Boot service provides in production; this mock is purely for E2E
pipeline testing.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

# Optional drivers — imported lazily inside per-dialect connect helpers so the
# mock service still starts when a specific driver is missing.  The
# ImportError surfaces only when a job actually targets that DB type.
try:
    import mysql.connector as _mysql  # type: ignore[import-not-found]
except ImportError:
    _mysql = None  # type: ignore[assignment]
try:
    import pymssql as _pymssql  # type: ignore[import-not-found]
except ImportError:
    _pymssql = None  # type: ignore[assignment]
try:
    import oracledb as _oracledb  # type: ignore[import-not-found]
except ImportError:
    _oracledb = None  # type: ignore[assignment]


# Default ports per dialect.
_DIALECT_PORT: dict[str, int] = {
    "postgres":  5432,
    "mysql":     3306,
    "sqlserver": 1433,
    "oracle":    1521,
}


def _connect(conn_cfg: dict, password: str, *, connect_timeout: int = 30):
    """Open a DBAPI connection for the requested ``type``.

    Returns ``(conn, dialect)`` where ``dialect`` is one of
    ``postgres / mysql / sqlserver / oracle``.  Raises ``ValueError`` on an
    unknown type, or ``ImportError`` when the matching driver isn't
    installed.
    """
    dialect = (conn_cfg.get("type") or "postgres").lower()
    host = conn_cfg["host"]
    port = int(conn_cfg.get("port") or _DIALECT_PORT.get(dialect, 5432))
    db = conn_cfg["database"]
    user = conn_cfg["user"]
    app_name = conn_cfg.get("application_name", "mock-extractor")

    if dialect == "postgres":
        return psycopg2.connect(
            host=host, port=port, dbname=db, user=user, password=password,
            application_name=app_name, connect_timeout=connect_timeout,
        ), dialect
    if dialect == "mysql":
        if _mysql is None:
            raise ImportError("mysql-connector-python not installed")
        return _mysql.connect(
            host=host, port=port, database=db, user=user, password=password,
            connection_timeout=connect_timeout,
        ), dialect
    if dialect == "sqlserver":
        if _pymssql is None:
            raise ImportError("pymssql not installed")
        return _pymssql.connect(
            server=host, port=str(port), database=db, user=user,
            password=password, login_timeout=connect_timeout,
            appname=app_name,
        ), dialect
    if dialect == "oracle":
        if _oracledb is None:
            raise ImportError("oracledb not installed")
        # Oracle uses a service name / SID supplied in `database`.
        dsn = _oracledb.makedsn(host, port, service_name=db)
        return _oracledb.connect(
            user=user, password=password, dsn=dsn,
        ), dialect
    raise ValueError(f"unsupported db type: {dialect!r}")


def _arrow_type_for(dialect: str, type_name: str) -> pa.DataType:
    """Map a dialect-specific type name to an Arrow type. Defaults to string."""
    t = (type_name or "").lower()
    # Common integer / numeric / temporal / boolean families across dialects.
    if any(k in t for k in ("bigint", "int8", "int_8")) or t == "long":
        return pa.int64()
    if "smallint" in t or "int2" in t or "tinyint" in t:
        return pa.int16()
    if "int" in t:  # plain int / integer / int4 / mediumint
        return pa.int32()
    if any(k in t for k in ("bool", "bit")) and "tinyint(1)" not in t:
        # mysql `bit(1)` and pg `bool` -> bool. Treat sqlserver `bit` as bool.
        if dialect == "sqlserver" and t == "bit":
            return pa.bool_()
        if dialect != "sqlserver" and "bool" in t:
            return pa.bool_()
    if "float8" in t or "double" in t:
        return pa.float64()
    if "float4" in t or "real" in t:
        return pa.float32()
    if any(k in t for k in ("numeric", "decimal", "number", "money")):
        return pa.float64()
    if "uuid" in t or "uniqueidentifier" in t:
        return pa.string()
    if "timestamp" in t and ("tz" in t or "with time zone" in t):
        return pa.timestamp("us", tz="UTC")
    if "timestamp" in t or "datetime" in t:
        return pa.timestamp("us")
    if t == "date":
        return pa.date32()
    if "bytea" in t or "blob" in t or "varbinary" in t or "image" in t or "raw" in t:
        return pa.binary()
    if "json" in t:
        return pa.string()
    return pa.string()

PORT = int(os.environ.get("MOCK_EXTRACTION_PORT", "8080"))
TOKEN = os.environ.get("EXTRACTION_SERVICE_TOKEN", "dev-token")
STORAGE_PATH = Path(os.environ.get("STORAGE_PATH", "/tmp/discovery-parquet"))
STORAGE_PATH.mkdir(parents=True, exist_ok=True)

# Source DB password resolution: only honour env:// secret refs (matches Java SecretResolver)
def resolve_secret(ref: str) -> str:
    if not ref:
        raise ValueError("password_secret_ref is empty")
    if ref.startswith("env://"):
        var = ref[len("env://"):]
        val = os.environ.get(var)
        if not val:
            raise ValueError(f"env var {var!r} is unset")
        return val
    if ref.startswith("vault://"):
        raise NotImplementedError("vault:// not implemented in mock")
    raise ValueError(f"unsupported secret ref scheme: {ref!r}")


# Cheap query safety check (the real service uses JSqlParser; this just blocks the obvious DML/DDL)
DANGEROUS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|VACUUM|COPY)\b",
    re.IGNORECASE,
)


def validate_query(q: str) -> None:
    if DANGEROUS.search(q):
        raise QueryRejected(f"query contains forbidden keyword")
    if not re.match(r"^\s*SELECT\b", q, re.IGNORECASE):
        raise QueryRejected("query must be a SELECT")


class QueryRejected(Exception):
    pass


def _resolve_pg_to_arrow(typ: str) -> pa.DataType:
    """Coarse Postgres oid type -> Arrow type mapping for COPY CSV output."""
    t = typ.lower()
    if "int" in t and "bigint" not in t:
        return pa.int32()
    if "bigint" in t or "int8" in t:
        return pa.int64()
    if "smallint" in t or "int2" in t:
        return pa.int16()
    if "bool" in t:
        return pa.bool_()
    if "uuid" in t:
        return pa.string()
    if t.startswith("numeric") or "decimal" in t:
        return pa.float64()
    if "double" in t or "float8" in t:
        return pa.float64()
    if "real" in t or "float4" in t:
        return pa.float32()
    if "timestamp" in t and "tz" in t:
        return pa.timestamp("us", tz="UTC")
    if "timestamp" in t:
        return pa.timestamp("us")
    if t == "date":
        return pa.date32()
    if "bytea" in t:
        return pa.binary()
    if "json" in t:
        return pa.string()
    return pa.string()


def extract_to_parquet(conn_cfg: dict, query: str, output_path: Path,
                       compression: str = "zstd",
                       compression_level: int = 3) -> dict:
    """Connect to source DB, run the query, write a Parquet file.

    Postgres uses the fast COPY path (csv stream straight into Arrow).
    MySQL / SQL Server / Oracle use a generic DBAPI cursor with
    ``fetchmany`` batches converted to Arrow record batches.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    # Prefer a per-request literal password (dev / API-supplied) when present;
    # otherwise resolve the env://VAR / vault://path reference.
    inline = conn_cfg.get("password_inline")
    if inline:
        password = inline
    else:
        password = resolve_secret(conn_cfg["password_secret_ref"])

    conn, dialect = _connect(conn_cfg, password)
    if dialect != "postgres":
        try:
            return _extract_dbapi(
                conn, dialect, query, output_path,
                compression=compression, compression_level=compression_level,
            )
        finally:
            conn.close()
    try:
        # First: introspect column types via a 0-row query
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM ({query}) AS _q LIMIT 0")
        descr = cur.description or []
        col_names = [d.name for d in descr]
        # pg_type oids for description.type_code → resolve to type name via pg_catalog
        type_oids = [d.type_code for d in descr]
        if type_oids:
            cur.execute("SELECT oid::int4, typname FROM pg_catalog.pg_type WHERE oid = ANY(%s)",
                        (type_oids,))
            oid_to_name = dict(cur.fetchall())
            col_types = [_resolve_pg_to_arrow(oid_to_name.get(o, "text")) for o in type_oids]
        else:
            col_types = []
        cur.close()

        if not col_names:
            # zero columns; write empty parquet
            schema = pa.schema([])
            with pq.ParquetWriter(str(output_path), schema, compression=compression) as w:
                pass
            return {
                "path": str(output_path), "rows": 0,
                "bytes": output_path.stat().st_size if output_path.exists() else 0,
                "checksum_sha256": _sha256(output_path), "row_groups": 0,
            }

        arrow_schema = pa.schema([pa.field(n, t) for n, t in zip(col_names, col_types)])

        # Run COPY
        copy_sql = f"COPY ({query}) TO STDOUT (FORMAT CSV, FORCE_QUOTE *, NULL '', HEADER FALSE)"
        cur = conn.cursor()
        buf = io.BytesIO()
        cur.copy_expert(copy_sql, buf)
        cur.close()
        buf.seek(0)

        # Convert CSV stream to Arrow with explicit schema
        read_opts = pa_csv.ReadOptions(column_names=col_names, block_size=8 << 20)
        parse_opts = pa_csv.ParseOptions(delimiter=",", quote_char='"', double_quote=True)
        # Force string columns into the right Arrow types via convert_options
        # PG COPY emits booleans as 't'/'f', not 'true'/'false' — teach pyarrow
        # to recognise both forms. Without this, BOOLEAN columns fail to parse
        # and the wholesale fallback to all-strings kicks in.
        convert_opts = pa_csv.ConvertOptions(
            column_types={n: t for n, t in zip(col_names, col_types)},
            null_values=[""],  # FORCE_QUOTE: unquoted-empty is the only way to encode NULL
            true_values=["t", "true", "TRUE", "T"],
            false_values=["f", "false", "FALSE", "F"],
            strings_can_be_null=True,
        )

        n_rows = 0
        n_groups = 0
        if buf.getbuffer().nbytes == 0:
            # zero rows
            with pq.ParquetWriter(str(output_path), arrow_schema, compression=compression,
                                  compression_level=compression_level) as w:
                pass
        else:
            try:
                table = pa_csv.read_csv(buf, read_options=read_opts, parse_options=parse_opts,
                                        convert_options=convert_opts)
            except Exception as e:
                # Fallback: read everything as strings; lets large catalog tables with weird
                # type encodings still produce a parquet file the pipeline can analyse.
                buf.seek(0)
                fallback_convert = pa_csv.ConvertOptions(
                    column_types={n: pa.string() for n in col_names},
                    null_values=[""],
                    strings_can_be_null=True,
                )
                read_opts2 = pa_csv.ReadOptions(column_names=col_names, block_size=8 << 20)
                table = pa_csv.read_csv(buf, read_options=read_opts2, parse_options=parse_opts,
                                        convert_options=fallback_convert)
                arrow_schema = table.schema
            n_rows = table.num_rows
            with pq.ParquetWriter(str(output_path), arrow_schema, compression=compression,
                                  compression_level=compression_level) as w:
                # 100K row groups
                for i in range(0, table.num_rows, 100_000):
                    chunk = table.slice(i, 100_000)
                    w.write_table(chunk)
                    n_groups += 1

        return {
            "path": str(output_path), "rows": n_rows,
            "bytes": output_path.stat().st_size,
            "checksum_sha256": _sha256(output_path), "row_groups": n_groups,
        }
    finally:
        conn.close()


def _extract_dbapi(conn, dialect: str, query: str, output_path: Path, *,
                   compression: str, compression_level: int) -> dict:
    """Generic DBAPI extraction: cursor.execute → fetchmany → Arrow batches.

    Used for every non-Postgres dialect.  Slower than Postgres COPY but
    portable across mysql-connector-python, pymssql and python-oracledb.
    """
    cur = conn.cursor()
    cur.execute(query)
    descr = cur.description or []
    if not descr:
        with pq.ParquetWriter(str(output_path), pa.schema([]),
                              compression=compression) as _w:
            pass
        return {
            "path": str(output_path), "rows": 0,
            "bytes": output_path.stat().st_size if output_path.exists() else 0,
            "checksum_sha256": _sha256(output_path), "row_groups": 0,
        }

    col_names: list[str] = []
    col_types: list[pa.DataType] = []
    for d in descr:
        # DBAPI's description is a 7-tuple; index 0 is name, 1 is type_code.
        # pymssql, mysql-connector and oracledb expose the same shape.
        name = d[0]
        type_code = d[1]
        # Best-effort: each driver returns its own type-code object — convert
        # to its repr/str and let _arrow_type_for() pattern-match keywords.
        type_name = ""
        try:
            type_name = type_code.__name__ if hasattr(type_code, "__name__") else str(type_code)
        except Exception:
            type_name = str(type_code)
        col_names.append(str(name))
        col_types.append(_arrow_type_for(dialect, type_name))

    arrow_schema = pa.schema(
        [pa.field(n, t) for n, t in zip(col_names, col_types)]
    )

    batch_size = 5_000
    total_rows = 0
    n_groups = 0
    # Use explicit try/finally instead of `with` so that when the schema-shift
    # path reassigns `writer` the new writer is guaranteed to be closed too.
    # The `with` context manager would only close the *original* writer on
    # exit, leaving the reassigned writer's file handle open on exceptions.
    writer = pq.ParquetWriter(str(output_path), arrow_schema,
                              compression=compression,
                              compression_level=compression_level)
    try:
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            cols: list[list] = [[] for _ in col_names]
            for r in rows:
                # rows can be tuples (most drivers) or dict-like (mysql Row).
                values = list(r) if not isinstance(r, dict) else [
                    r.get(n) for n in col_names
                ]
                # Pad / truncate defensively.
                if len(values) < len(col_names):
                    values = values + [None] * (len(col_names) - len(values))
                for i, v in enumerate(values[: len(col_names)]):
                    cols[i].append(v)
            arrays = []
            for i, t in enumerate(col_types):
                try:
                    arrays.append(pa.array(cols[i], type=t))
                except (pa.ArrowInvalid, pa.ArrowTypeError, ValueError):
                    # Fallback to string when the values don't fit the inferred
                    # Arrow type (common for Oracle NUMBER, MySQL JSON, etc).
                    arrays.append(pa.array(
                        [None if v is None else str(v) for v in cols[i]],
                        type=pa.string(),
                    ))
                    # Rewrite the schema slot to string for consistency.
                    col_types[i] = pa.string()
                    arrow_schema = pa.schema([
                        pa.field(n, t) for n, t in zip(col_names, col_types)
                    ])
            batch = pa.RecordBatch.from_arrays(arrays, names=col_names)
            try:
                writer.write_batch(batch)
            except (pa.ArrowInvalid, pa.ArrowTypeError):
                # Schema may have shifted to string mid-stream; reopen the
                # writer from-scratch with the relaxed schema.
                writer.close()
                # Re-init with the (possibly relaxed) schema and replay this
                # batch only. Earlier rows already on disk are kept.
                writer = pq.ParquetWriter(
                    str(output_path), arrow_schema,
                    compression=compression, compression_level=compression_level,
                )
                writer.write_batch(batch)
            total_rows += batch.num_rows
            n_groups += 1
    finally:
        writer.close()
    cur.close()
    return {
        "path": str(output_path), "rows": total_rows,
        "bytes": output_path.stat().st_size,
        "checksum_sha256": _sha256(output_path), "row_groups": n_groups,
    }


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- HTTP layer ----------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, body: dict | None) -> None:
        data = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def _check_auth(self) -> bool:
        if self.path.startswith("/actuator/health") or self.path.startswith("/actuator/info"):
            return True
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_json(401, {"code": "UNAUTHORIZED", "message": "missing bearer", "retryable": False})
            return False
        token = auth[len("Bearer "):]
        # constant-time compare
        if len(token) != len(TOKEN) or sum(a != b for a, b in zip(token, TOKEN)) != 0:
            self._send_json(401, {"code": "UNAUTHORIZED", "message": "invalid bearer", "retryable": False})
            return False
        return True

    def do_GET(self):
        if self.path == "/actuator/health":
            self._send_json(200, {"status": "UP"})
            return
        if not self._check_auth():
            return
        self._send_json(404, {"code": "NOT_FOUND", "message": self.path, "retryable": False})

    def do_POST(self):
        if not self._check_auth():
            return
        try:
            body = self._read_body()
        except Exception as e:
            self._send_json(400, {"code": "BAD_REQUEST", "message": f"invalid json: {e}", "retryable": False})
            return

        try:
            if self.path == "/api/v1/connections/test":
                cfg = body
                inline = cfg.get("password_inline")
                password = inline if inline else resolve_secret(cfg["password_secret_ref"])
                conn, _dialect = _connect(cfg, password, connect_timeout=10)
                conn.close()
                self._send_json(200, {"status": "ok"})
                return

            if self.path == "/api/v1/extract":
                conn_cfg = body["connection"]
                query = body["query"]
                out_cfg = body["output"]
                opts = body.get("options") or {}
                tag = opts.get("tag") or ""

                validate_query(query)

                output_path = Path(out_cfg["path"])
                compression = out_cfg.get("compression", "zstd")
                clevel = out_cfg.get("compression_level", 3)

                eid = str(uuid.uuid4())
                t0 = time.time()
                manifest_entry = extract_to_parquet(conn_cfg, query, output_path,
                                                     compression=compression,
                                                     compression_level=clevel)
                duration_ms = int((time.time() - t0) * 1000)
                resp = {
                    "extraction_id": eid,
                    "status": "completed",
                    "manifest": {
                        "files": [manifest_entry],
                        "duration_ms": duration_ms,
                        "rows_per_second": int(manifest_entry["rows"] / max(duration_ms / 1000.0, 0.001)),
                        "bytes_per_second": int(manifest_entry["bytes"] / max(duration_ms / 1000.0, 0.001)),
                    },
                    "error": None,
                }
                self._send_json(200, resp)
                return

            self._send_json(404, {"code": "NOT_FOUND", "message": self.path, "retryable": False})
        except QueryRejected as e:
            self._send_json(400, {"code": "QUERY_NOT_ALLOWED", "message": str(e), "retryable": False})
        except KeyError as e:
            self._send_json(400, {"code": "BAD_REQUEST", "message": f"missing field: {e}", "retryable": False})
        except Exception as e:
            traceback.print_exc()
            self._send_json(500, {"code": "INTERNAL", "message": str(e), "retryable": True})

    def log_message(self, fmt, *args):
        # Quiet default access logging; we just print structured lines
        sys.stderr.write(f"[mock] {self.address_string()} {fmt % args}\n")


def main():
    print(f"[mock] listening on :{PORT}")
    print(f"[mock] STORAGE_PATH = {STORAGE_PATH}")
    print(f"[mock] auth: bearer {TOKEN[:4]}…")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
