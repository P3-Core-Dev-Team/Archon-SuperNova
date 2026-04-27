"""Test that running generation twice with the same seed produces identical output."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import pytest

SYNTHETIC_DIR = Path(os.environ.get("SYNTHETIC_DIR", "./synthetic"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_generation(output_dir: Path, seed: int = 42) -> dict[str, str]:
    """Run generation and return {filename: sha256} mapping."""
    from synthetic_data.runner import run_generation
    run_generation(
        output_dir=output_dir,
        seed=seed,
        compression="zstd",
        compression_level=3,
        small=True,  # Use small mode for speed in tests
    )
    schemas_dir = output_dir / "schemas"
    return {
        p.name: _sha256_file(p)
        for p in sorted(schemas_dir.glob("*.parquet"))
    }


def test_determinism_same_seed():
    """Two runs with seed=42 must produce byte-identical Parquet files."""
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        checksums1 = _run_generation(Path(tmp1), seed=42)
        checksums2 = _run_generation(Path(tmp2), seed=42)

        assert set(checksums1.keys()) == set(checksums2.keys()), (
            f"Different file sets: {set(checksums1.keys()) ^ set(checksums2.keys())}"
        )

        mismatches = {
            name: (checksums1[name], checksums2[name])
            for name in checksums1
            if checksums1[name] != checksums2[name]
        }
        assert len(mismatches) == 0, (
            f"Non-deterministic files (sha256 differs): {list(mismatches.keys())}"
        )


def test_determinism_different_seeds_differ():
    """Two runs with different seeds must produce different output."""
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        checksums42 = _run_generation(Path(tmp1), seed=42)
        checksums99 = _run_generation(Path(tmp2), seed=99)

        # At least some files should differ
        matching = [
            name for name in checksums42
            if name in checksums99 and checksums42[name] == checksums99[name]
        ]
        # Most files should differ (only empty_table might be identical)
        differ_count = len(checksums42) - len(matching)
        assert differ_count > len(checksums42) // 2, (
            f"Seeds 42 and 99 produce too many identical files ({len(matching)}/{len(checksums42)})"
        )
