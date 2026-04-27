"""Ground truth manifest writer and Pydantic validator."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from synthetic_data.config import (
    ALL_TABLES,
    TABLE_REGISTRY,
    TableSpec,
    ColumnSpec,
    TypeClass,
    PiiType,
)

# ---------------------------------------------------------------------------
# Pydantic models for ground_truth.json
# ---------------------------------------------------------------------------


class ColumnManifest(BaseModel):
    name: str
    type_class: str
    is_pk: bool = False
    is_fk_eligible: bool = True
    nullable: bool = False
    null_pct: float = 0.0
    pii: list[str] = Field(default_factory=list)
    pii_rate: float = 0.0


class TableManifest(BaseModel):
    name: str
    rows: int
    excluded: bool = False
    exclusion_reason: Optional[str] = None
    columns: list[ColumnManifest]


class ForeignKeyManifest(BaseModel):
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    cardinality: str = "MANY_TO_ONE"
    containment: float = 1.0
    null_pct: float = 0.0


class ExclusionManifest(BaseModel):
    table: str
    reason: str


class GroundTruth(BaseModel):
    generator_version: str = "1.0.0"
    seed: int
    generated_at: str
    tables: list[TableManifest]
    expected_foreign_keys: list[ForeignKeyManifest]
    expected_exclusions: list[ExclusionManifest]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _col_to_manifest(col: ColumnSpec) -> ColumnManifest:
    return ColumnManifest(
        name=col.name,
        type_class=col.type_class.value,
        is_pk=col.is_pk,
        is_fk_eligible=col.is_fk_eligible,
        nullable=col.nullable,
        null_pct=col.null_pct,
        pii=[p.value for p in col.pii_types],
        pii_rate=col.pii_rate,
    )


def build_ground_truth(seed: int, actual_row_counts: dict[str, int]) -> GroundTruth:
    """Build the GroundTruth manifest from config."""
    tables = []
    foreign_keys = []
    exclusions = []

    for spec in ALL_TABLES:
        actual_rows = actual_row_counts.get(spec.name, spec.row_count)
        table_manifest = TableManifest(
            name=spec.name,
            rows=actual_rows,
            excluded=spec.excluded,
            exclusion_reason=spec.exclusion_reason,
            columns=[_col_to_manifest(c) for c in spec.columns],
        )
        tables.append(table_manifest)

        if spec.excluded:
            exclusions.append(ExclusionManifest(
                table=spec.name,
                reason=spec.exclusion_reason or "pattern",
            ))

        # Collect FK relationships
        for col in spec.columns:
            if col.fk is not None:
                foreign_keys.append(ForeignKeyManifest(
                    child_table=spec.name,
                    child_column=col.name,
                    parent_table=col.fk.parent_table,
                    parent_column=col.fk.parent_column,
                    cardinality="MANY_TO_ONE",
                    containment=1.0,
                    null_pct=col.fk.null_pct,
                ))

    return GroundTruth(
        seed=seed,
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        tables=tables,
        expected_foreign_keys=foreign_keys,
        expected_exclusions=exclusions,
    )


def write_ground_truth(
    output_dir: Path,
    seed: int,
    actual_row_counts: dict[str, int],
) -> Path:
    """Write ground_truth.json to output_dir. Returns path written."""
    gt = build_ground_truth(seed, actual_row_counts)
    out_path = output_dir / "ground_truth.json"
    out_path.write_text(
        gt.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return out_path
