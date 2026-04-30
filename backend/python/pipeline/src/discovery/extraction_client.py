"""
extraction_client.py — httpx-based client for the Spring Boot Extraction Service.

Public surface (matches the OpenAPI v1.1.0 sync-only contract):
  - test_connection(conn) — POST /api/v1/connections/test
  - extract_sync(req)     — POST /api/v1/extract

There is no async path, no cancel endpoint, and no status-poll endpoint in the
v1.1.0 contract — the synchronous   /extract response carries the full manifest.

Security guarantees:
  - ConnectionConfig is logged with password_secret_ref REDACTED.
  - Authorization header value is never logged.
  - All log events go through the redact_secrets processor in logging_setup.py.

Retry policy (tenacity):
  - Retries on network errors (TransportError, ConnectError, TimeoutException)
    and on HTTP 5xx responses.
  - Does NOT retry on HTTP 4xx (client errors are not transient).
  - 3 attempts with exponential backoff: 5s, 10s (jitter added by tenacity).
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from discovery.models import (
    ConnectionConfig,
    ExtractionRequest,
    ExtractionResponse,
)

log = structlog.get_logger("discovery.extraction_client")


# ---------------------------------------------------------------------------
# Retry predicate
# ---------------------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    """Return True iff the exception should trigger a retry."""
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


def _log_retry(retry_state: RetryCallState) -> None:
    """Log retry attempt without exposing sensitive context."""
    log.warning(
        "extraction_client.retry",
        attempt=retry_state.attempt_number,
        exception_type=type(retry_state.outcome.exception()).__name__
        if retry_state.outcome
        else None,
    )


# ---------------------------------------------------------------------------
# Safe representation of ConnectionConfig for logging
# ---------------------------------------------------------------------------


def _safe_conn_repr(conn: ConnectionConfig) -> dict[str, Any]:
    """Return a loggable dict from ConnectionConfig with both credential
    channels redacted (secret ref AND any literal inline password)."""
    return {
        "type": conn.type,
        "host": conn.host,
        "port": conn.port,
        "database": conn.database,
        "user": conn.user,
        "password_secret_ref": "***redacted***",
        "password_inline": "***redacted***" if conn.password_inline else None,
        "ssl_mode": conn.ssl_mode,
    }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ExtractionClient:
    """HTTP client for the Spring Boot Discovery Extraction Service."""

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        request_timeout_seconds: int = 7200,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {auth_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(
                connect=30.0,
                read=float(request_timeout_seconds),
                write=60.0,
                pool=10.0,
            ),
        )

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __enter__(self) -> "ExtractionClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_connection(self, conn: ConnectionConfig) -> None:
        """Validate a connection config without performing any extraction."""
        log.info(
            "extraction_client.test_connection",
            conn=_safe_conn_repr(conn),
        )
        response = self._post_with_retry(
            "/api/v1/connections/test",
            body=conn.model_dump(mode="json"),
        )
        response.raise_for_status()
        log.info("extraction_client.test_connection.ok", conn_host=conn.host)

    def probe_cardinality(
        self,
        conn: ConnectionConfig,
        pairs: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Issue a live cardinality probe.

        Body shape:
            { "connection": {...}, "pairs": [{schema, table, column}, ...] }

        Response shape:
            { "results": [{schema, table, column, total_rows,
                           distinct_count}, ...] }

        Returns the bare ``results`` list.  Pairs that the service
        couldn't probe (table missing, permission error, identifier
        rejected) are simply absent from the result; the caller
        handles partial success.

        This endpoint is gated on the discovery side by
        ``RelationshipsConfig.cardinality_refine_enabled`` (default
        False).  If the extraction service version doesn't expose
        ``/probe-cardinality`` yet, the call surfaces a 404 which the
        phase swallows so the pipeline doesn't fail.
        """
        log.info(
            "extraction_client.probe_cardinality.start",
            conn=_safe_conn_repr(conn),
            pair_count=len(pairs),
        )
        response = self._post_with_retry(
            "/api/v1/probe-cardinality",
            body={
                "connection": conn.model_dump(mode="json"),
                "pairs": pairs,
            },
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        log.info(
            "extraction_client.probe_cardinality.done",
            requested=len(pairs),
            returned=len(results),
        )
        return results

    def extract_sync(self, req: ExtractionRequest) -> ExtractionResponse:
        """Submit a synchronous extraction request and block until it completes."""
        log.info(
            "extraction_client.extract_sync.start",
            conn=_safe_conn_repr(req.connection),
            query=req.query,
            output_path=req.output.path,
        )
        response = self._post_with_retry(
            "/api/v1/extract",
            body=req.model_dump(mode="json"),
        )
        response.raise_for_status()
        result = ExtractionResponse.model_validate(response.json())
        log.info(
            "extraction_client.extract_sync.done",
            extraction_id=result.extraction_id,
            status=result.status,
            total_rows=result.manifest.total_rows if result.manifest else None,
            total_bytes=result.manifest.total_bytes if result.manifest else None,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers with tenacity retry
    # ------------------------------------------------------------------

    def _post_with_retry(self, path: str, body: dict[str, Any]) -> httpx.Response:
        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=5, max=60),
            before_sleep=_log_retry,
            reraise=True,
        )
        def _do() -> httpx.Response:
            resp = self._client.post(path, json=body)
            if resp.status_code >= 400:
                resp.raise_for_status()
            return resp

        return _do()
