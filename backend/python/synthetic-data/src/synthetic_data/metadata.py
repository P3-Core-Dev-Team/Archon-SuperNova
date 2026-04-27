"""Metadata writer: generator_version, seed, timestamp, per-table byte sizes."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from synthetic_data import __version__


def write_metadata(
    output_dir: Path,
    seed: int,
    actual_row_counts: dict[str, int],
    schemas_dir: Path,
) -> Path:
    """
    Write metadata.json to output_dir.
    Reads file sizes from the schemas/ directory.
    Returns path written.
    """
    table_sizes: dict[str, dict] = {}
    for table_name, rows in actual_row_counts.items():
        parquet_path = schemas_dir / f"{table_name}.parquet"
        size_bytes = parquet_path.stat().st_size if parquet_path.exists() else 0
        table_sizes[table_name] = {
            "rows": rows,
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 3),
        }

    total_bytes = sum(v["size_bytes"] for v in table_sizes.values())
    total_rows = sum(v["rows"] for v in table_sizes.values())

    metadata = {
        "generator_version": __version__,
        "seed": seed,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_rows": total_rows,
        "total_size_bytes": total_bytes,
        "total_size_mb": round(total_bytes / (1024 * 1024), 3),
        "tables": table_sizes,
    }

    out_path = output_dir / "metadata.json"
    out_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return out_path
