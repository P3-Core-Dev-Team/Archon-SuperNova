# Synthetic Data Generator

Generates realistic Parquet files simulating a 30-table enterprise schema for discovery pipeline POC testing.

## Installation

```bash
cd synthetic-data
pip install -e ".[test]"
```

## Usage

```bash
# Full generation (seed 42, zstd compression level 3)
python -m synthetic_data generate --output-dir ./synthetic --seed 42 --compression zstd --compression-level 3

# Quick smoke test (10x smaller row counts)
python -m synthetic_data generate --output-dir ./synthetic-small --seed 42 --small
```

## Output Layout

```
synthetic/
├── ground_truth.json       # manifest: tables, FKs, PII columns, exclusions
├── metadata.json           # generator version, seed, timestamp, per-table sizes
└── schemas/
    ├── customers.parquet
    ├── addresses.parquet
    ├── products.parquet
    ├── categories.parquet
    ├── orders.parquet
    ├── order_items.parquet
    ├── payments.parquet
    ├── inventory.parquet
    ├── warehouses.parquet
    ├── warehouse_stock.parquet
    ├── users.parquet
    ├── user_roles.parquet
    ├── roles.parquet
    ├── user_sessions.parquet
    ├── api_tokens.parquet
    ├── employee_records.parquet
    ├── departments.parquet
    ├── tickets.parquet
    ├── ticket_messages.parquet
    ├── reviews.parquet
    ├── audit_log.parquet
    ├── access_log.parquet
    ├── temp_import_batch.parquet
    ├── tmp_staging_orders.parquet
    ├── orders_bak_20240101.parquet
    ├── customers_archive.parquet
    ├── user_events.parquet
    ├── etl_import_queue.parquet
    ├── migrations.parquet
    ├── wide_denormalized.parquet
    └── empty_table.parquet
```

## Running Tests

```bash
# Must run generation first
python -m synthetic_data generate --output-dir ./synthetic --seed 42 --small

# Run tests
pytest tests/ -v

# Run individual test suites
pytest tests/test_pii_validity.py -v
pytest tests/test_fk_containment.py -v
pytest tests/test_determinism.py -v
pytest tests/test_manifest_schema.py -v
```

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--output-dir` | `./synthetic` | Output directory |
| `--seed` | `42` | Random seed for reproducibility |
| `--compression` | `zstd` | Parquet compression codec |
| `--compression-level` | `3` | Compression level |
| `--small` | `False` | Scale rows by 0.1 for quick smoke tests |
