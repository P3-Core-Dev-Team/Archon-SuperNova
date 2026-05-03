"""
Pytest configuration and fixtures for discovery pipeline tests.

Run tests:
  pytest                           # Unit tests only
  pytest -m integration            # Integration tests (requires Docker)
  pytest -m "not integration"      # Skip integration tests
  pytest --cov=discovery           # With coverage report
"""

from pathlib import Path
from typing import Generator

import httpx
import pytest
import structlog
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def pytest_configure(config: pytest.Config) -> None:
    """Register the 'integration' marker so `-m "not integration"` works."""
    config.addinivalue_line(
        "markers",
        "integration: tests that require Docker / testcontainers",
    )


@pytest.fixture(scope="session")
def results_db_url() -> Generator[str, None, None]:
    """
    Spin up an ephemeral Postgres container for results DB.
    Initialize schema from results_schema.sql.
    Yields the DSN (connection string).
    """
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    container = PostgresContainer("postgres:15-alpine")
    try:
        container.start()
    except Exception as exc:  # Docker unavailable, etc.
        pytest.skip(f"Cannot start Postgres container: {exc}")

    try:
        dsn = container.get_connection_url()

        schema_path = Path(__file__).parent.parent / "sql" / "results_schema.sql"
        if schema_path.exists():
            import psycopg2

            with open(schema_path, "r") as f:
                schema_sql = f.read()

            conn = psycopg2.connect(dsn)
            cur = conn.cursor()
            cur.execute(schema_sql)
            conn.commit()
            cur.close()
            conn.close()

        yield dsn
    finally:
        container.stop()


@pytest.fixture(scope="session")
def source_db_url() -> Generator[str, None, None]:
    """Spin up an ephemeral Postgres container for the source DB."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    container = PostgresContainer("postgres:15-alpine")
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"Cannot start Postgres container: {exc}")

    try:
        dsn = container.get_connection_url()

        seed_path = Path(__file__).parent / "fixtures" / "seed_source.sql"
        if seed_path.exists():
            import psycopg2

            with open(seed_path, "r") as f:
                seed_sql = f.read()

            conn = psycopg2.connect(dsn)
            cur = conn.cursor()
            cur.execute(seed_sql)
            conn.commit()
            cur.close()
            conn.close()

        yield dsn
    finally:
        container.stop()


@pytest.fixture
def engine(results_db_url: str) -> Engine:
    """
    Create a SQLAlchemy engine for results DB.
    Scoped to function lifetime.
    """
    eng = create_engine(results_db_url, echo=False)
    yield eng
    eng.dispose()


@pytest.fixture
def tmp_parquet_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Provide a temporary directory for parquet files.
    Create a tiny 2-column, 5-row parquet fixture for fingerprint/validate tests.
    """
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)

    # Create tiny test parquet
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        # Simple 2-column table with 5 rows
        data = {
            "id": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
            "name": pa.array(["Alice", "Bob", "Charlie", "Diana", "Eve"], type=pa.string()),
        }
        table = pa.Table.from_pydict(data)

        fixture_path = parquet_dir / "fixture.parquet"
        pq.write_table(table, str(fixture_path), compression="zstd")
    except ImportError:
        # pyarrow not available yet, skip fixture creation
        pass

    yield parquet_dir


@pytest.fixture
def mock_extraction_client() -> httpx.Client:
    """
    Create an httpx.Client with MockTransport for stub API responses.
    Supports /api/v1/extract and /api/v1/connections/test endpoints.
    """

    def mock_transport(request: httpx.Request) -> httpx.Response:
        """Mock responses for extraction service endpoints."""
        if request.url.path == "/api/v1/extract":
            # Return a stub extraction response
            return httpx.Response(
                status_code=200,
                json={
                    "manifest_id": "test-manifest-001",
                    "table_name": "test_table",
                    "row_count": 100,
                    "parquet_path": "/tmp/test.parquet",
                    "status": "completed",
                },
            )
        elif request.url.path == "/api/v1/connections/test":
            # Return success for connection test
            return httpx.Response(
                status_code=200,
                json={"status": "ok", "latency_ms": 5},
            )
        else:
            # Unrecognized endpoint
            return httpx.Response(status_code=404, json={"error": "not found"})

    transport = httpx.MockTransport(mock_transport)
    client = httpx.Client(transport=transport, base_url="http://localhost:8080")
    yield client
    client.close()


@pytest.fixture(autouse=True)
def caplog_structlog(caplog):
    """
    Automatically route structlog output to pytest's caplog.
    This allows tests to assert on structured log messages.
    """
    handler = structlog.PrintLoggerFactory()

    # Capture structlog output
    structlog.configure(
        processors=[
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    yield caplog
