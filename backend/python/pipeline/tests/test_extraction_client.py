"""
test_extraction_client.py — ExtractionClient tests using httpx MockTransport.

Tests:
  - Authorization header is always sent
  - test_connection() calls correct endpoint
  - extract_sync() returns ExtractionResponse
  - Retry on 500 (3 attempts), no retry on 400
  - Password never appears in any log output

Note: the v1.1.0 contract is sync-only.  No async / cancel / status-poll tests.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from discovery.extraction_client import ExtractionClient
from discovery.models import (
    ConnectionConfig,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionStatus,
    ManifestEntry,
    OutputConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_CONN = ConnectionConfig(
    type="postgres",
    host="source-db",
    port=5432,
    database="source",
    user="reader",
    password_secret_ref="env://SOURCE_DB_PASSWORD",
    ssl_mode="require",
)

MOCK_REQUEST = ExtractionRequest(
    connection=MOCK_CONN,
    query="SELECT * FROM public.customers",
    output=OutputConfig(path="/data/parquet/customers.parquet"),
)

_COMPLETED_RESPONSE = {
    "extraction_id": "aaaabbbb-cccc-dddd-eeee-ffffffffffff",
    "status": "completed",
    "manifest": {
        "files": [
            {
                "path": "/data/parquet/customers.parquet",
                "rows": 1000,
                "bytes": 512000,
                "checksum_sha256": "abc123",
                "row_groups": 1,
            }
        ],
        "duration_ms": 500,
        "rows_per_second": 2000,
        "bytes_per_second": 1024000,
    },
    "error": None,
}

def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> ExtractionClient:
    """Build an ExtractionClient backed by a mock transport."""
    transport = httpx.MockTransport(handler)
    client = ExtractionClient.__new__(ExtractionClient)
    client._base_url = "http://mock"
    client._client = httpx.Client(
        base_url="http://mock",
        transport=transport,
        headers={"Authorization": "Bearer test-token"},
    )
    return client


# ---------------------------------------------------------------------------
# Helper to capture all log events
# ---------------------------------------------------------------------------


class LogCapture:
    """Captures structlog / stdlib log records for assertion."""

    def __init__(self) -> None:
        self.records: list[str] = []

    def handler(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())

    @property
    def full_text(self) -> str:
        return "\n".join(self.records)


# ---------------------------------------------------------------------------
# Tests: Authorization header
# ---------------------------------------------------------------------------


def test_authorization_header_always_sent() -> None:
    """Every request must include the Authorization header."""
    received_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_headers.append(dict(request.headers))
        return httpx.Response(200, json={"status": "ok"})

    with ExtractionClient("http://mock", "my-secret-token", request_timeout_seconds=5) as client:
        client._client = httpx.Client(
            base_url="http://mock",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer my-secret-token"},
        )
        try:
            client.test_connection(MOCK_CONN)
        except Exception:
            pass

    assert any("authorization" in h for h in received_headers), (
        "Authorization header was not sent"
    )
    for headers in received_headers:
        auth = headers.get("authorization", "")
        assert "my-secret-token" in auth


# ---------------------------------------------------------------------------
# Tests: test_connection
# ---------------------------------------------------------------------------


def test_test_connection_ok() -> None:
    """test_connection() succeeds on 200."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        assert request.url.path == "/api/v1/connections/test"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["host"] == "source-db"
        return httpx.Response(200, json={"status": "ok"})

    client = _make_client(handler)
    client.test_connection(MOCK_CONN)
    assert call_count == 1


def test_test_connection_400_raises() -> None:
    """test_connection() raises on 400 (not retried)."""
    attempt_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        return httpx.Response(
            400,
            json={"code": "INVALID_HOST", "message": "host not reachable", "retryable": False},
        )

    client = _make_client(handler)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.test_connection(MOCK_CONN)

    # Should NOT retry on 400
    assert attempt_count == 1
    assert exc_info.value.response.status_code == 400


# ---------------------------------------------------------------------------
# Tests: extract_sync
# ---------------------------------------------------------------------------


def test_extract_sync_returns_response() -> None:
    """extract_sync() returns a parsed ExtractionResponse."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/extract"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["query"] == MOCK_REQUEST.query
        return httpx.Response(200, json=_COMPLETED_RESPONSE)

    client = _make_client(handler)
    resp = client.extract_sync(MOCK_REQUEST)

    assert resp.extraction_id == "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
    assert resp.status == ExtractionStatus.COMPLETED
    assert resp.manifest is not None
    assert resp.manifest.total_rows == 1000
    assert resp.manifest.total_bytes == 512000


# ---------------------------------------------------------------------------
# Tests: Retry on 500
# ---------------------------------------------------------------------------


def test_retry_on_500_three_attempts() -> None:
    """extract_sync() retries on 500 up to 3 times total (first call + 2 retries)."""
    attempt_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        return httpx.Response(500, json={"error": "Internal Server Error"})

    client = _make_client(handler)

    # Patch tenacity wait to speed up tests
    with patch("tenacity.wait_exponential.__call__", return_value=0):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.extract_sync(MOCK_REQUEST)

    # 3 total attempts (initial + 2 retries)
    assert attempt_count == 3
    assert exc_info.value.response.status_code == 500


def test_no_retry_on_400() -> None:
    """extract_sync() does NOT retry on 400."""
    attempt_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        return httpx.Response(
            400,
            json={"code": "QUERY_REJECTED", "message": "JOIN not allowed"},
        )

    client = _make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.extract_sync(MOCK_REQUEST)

    assert attempt_count == 1, f"Expected 1 attempt on 400, got {attempt_count}"


# ---------------------------------------------------------------------------
# Tests: Password never in logs
# ---------------------------------------------------------------------------


def test_password_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    """
    The password_secret_ref value must never appear in any log output.

    Even though this field is only a reference (env://VAR), we ensure it's
    redacted in log output as defence-in-depth.
    """
    conn_with_sensitive_ref = ConnectionConfig(
        type="postgres",
        host="db",
        port=5432,
        database="src",
        user="user",
        password_secret_ref="env://MY_SUPER_SECRET_VAR",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    with caplog.at_level(logging.DEBUG, logger="discovery"):
        client = _make_client(handler)
        client.test_connection(conn_with_sensitive_ref)

    # The actual secret ref value must not appear in logs
    for record in caplog.records:
        assert "MY_SUPER_SECRET_VAR" not in record.getMessage(), (
            f"Secret ref leaked into log: {record.getMessage()!r}"
        )


def test_connection_payload_does_not_include_raw_password() -> None:
    """
    The JSON body sent to the service must include password_secret_ref,
    not a resolved password field.
    """
    sent_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent_body
        sent_body = json.loads(request.content)
        return httpx.Response(200, json={"status": "ok"})

    client = _make_client(handler)
    client.test_connection(MOCK_CONN)

    # Must have password_secret_ref, NOT a 'password' key
    assert "password_secret_ref" in sent_body
    assert "password" not in sent_body, (
        "Raw 'password' field was sent in the connection payload"
    )
