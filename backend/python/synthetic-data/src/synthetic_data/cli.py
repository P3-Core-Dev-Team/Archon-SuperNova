"""Typer CLI for the synthetic data generator."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="synthetic-data",
    help="Generate realistic synthetic Parquet files for discovery pipeline POC.",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
)


def _generate_impl(
    output_dir: Path,
    seed: int,
    compression: str,
    compression_level: int,
    small: bool,
) -> None:
    from synthetic_data.runner import run_generation

    typer.echo(f"Generating synthetic data → {output_dir} (seed={seed}, small={small})")
    actual_row_counts = run_generation(
        output_dir=output_dir,
        seed=seed,
        compression=compression,
        compression_level=compression_level,
        small=small,
    )
    total = sum(actual_row_counts.values())
    typer.echo(f"\nComplete. {total:,} total rows in {len(actual_row_counts)} tables.")


@app.command("generate")
def generate(
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Output directory for generated files"),
    ] = Path("./synthetic"),
    seed: Annotated[
        int,
        typer.Option("--seed", "-s", help="Random seed for reproducibility"),
    ] = 42,
    compression: Annotated[
        str,
        typer.Option("--compression", help="Parquet compression codec"),
    ] = "zstd",
    compression_level: Annotated[
        int,
        typer.Option("--compression-level", help="Compression level (codec-dependent)"),
    ] = 3,
    small: Annotated[
        bool,
        typer.Option("--small/--no-small", help="Scale row counts by 0.1 for quick tests"),
    ] = False,
) -> None:
    """Generate synthetic Parquet files for all 30 tables."""
    _generate_impl(output_dir, seed, compression, compression_level, small)


@app.callback()
def _default(
    ctx: typer.Context,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Output directory for generated files"),
    ] = Path("./synthetic"),
    seed: Annotated[
        int,
        typer.Option("--seed", "-s", help="Random seed for reproducibility"),
    ] = 42,
    compression: Annotated[
        str,
        typer.Option("--compression", help="Parquet compression codec"),
    ] = "zstd",
    compression_level: Annotated[
        int,
        typer.Option("--compression-level", help="Compression level (codec-dependent)"),
    ] = 3,
    small: Annotated[
        bool,
        typer.Option("--small/--no-small", help="Scale row counts by 0.1 for quick tests"),
    ] = False,
) -> None:
    """Generate synthetic Parquet files for all 30 tables (default action when no subcommand given)."""
    if ctx.invoked_subcommand is not None:
        # A subcommand was invoked; let it handle the work.
        return
    _generate_impl(output_dir, seed, compression, compression_level, small)


if __name__ == "__main__":
    app()
