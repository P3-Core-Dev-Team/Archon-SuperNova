"""
cli.py — Typer CLI for the Discovery pipeline.

Subcommands
-----------
    discovery init                   # create schema, verify connectivity
    discovery inventory              # Phase 1
    discovery extract [--limit N]    # Phase 2
    discovery fingerprint            # Phase 3a
    discovery pii-scan               # Phase 3b
    discovery generate-candidates    # Phase 4
    discovery validate [--limit N]   # Phase 5
    discovery report <sub>           # Phase 7 (relationships|pii|exclusions|all)
    discovery run-all [--limit N]    # Phases 1–7 fully resumable
                       [--two-pass]  #   triage on a 1% sample, then
                                     #   full-extract surviving tables only
                       [--sample-pct FLOAT]  # 1.0 = 1% (used with --two-pass)
    discovery status                 # progress per phase from run_log
    discovery cleanup [--dry-run]    # GC orphaned parquet (no surviving FK candidate)
                      [--keep-results]  # also wipe results DB on full purge
                      [--purge]      # nuclear: rmtree + drop schema

Global options (callback on the app)
-------------------------------------
    --config PATH       Path to YAML config (default: search ./config/default.yaml
                        then $XDG_CONFIG_HOME/discovery/default.yaml)
    --dry-run           Print planned actions, do nothing
    --log-level LEVEL   DEBUG|INFO|WARNING|ERROR  (default INFO)
    --json-logs         Structured JSON output  (default)
    --text-logs         Human-readable console output

Conventions
-----------
* Every command loads config and configures logging first.
* ``--dry-run`` short-circuits before any IO (no engine, no HTTP client).
* Phase module imports are deferred inside command bodies so CLI is importable
  even when sibling-agent modules are not yet installed.
* ``typer.secho`` for short user-facing status; structlog for structured events.
* Secrets (passwords, tokens) are redacted in the config summary logged at
  init time — see ``_redacted_config_summary``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import structlog
import typer
from typing_extensions import Annotated

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# App definition
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="discovery",
    help="Discovery Pipeline CLI — crawl a database, find FKs and PII.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,  # keep raw tracebacks for structured logs
)

# Sub-app for `discovery report`
report_app = typer.Typer(
    name="report",
    help="Phase 7: generate relationship, PII, exclusion, and summary reports.",
    no_args_is_help=True,
)
app.add_typer(report_app, name="report")

# ---------------------------------------------------------------------------
# Type aliases for shared option types
# ---------------------------------------------------------------------------

ConfigOption = Annotated[
    Optional[Path],
    typer.Option(
        "--config",
        "-c",
        help=(
            "Path to YAML config file. "
            "Defaults: ./config/default.yaml or "
            "$XDG_CONFIG_HOME/discovery/default.yaml"
        ),
        show_default=False,
    ),
]

DryRunOption = Annotated[
    bool,
    typer.Option(
        "--dry-run/--no-dry-run",
        help="Print planned actions without executing them.",
    ),
]

LogLevelOption = Annotated[
    str,
    typer.Option(
        "--log-level",
        help="Logging level: DEBUG, INFO, WARNING, ERROR.",
        show_default=True,
    ),
]

LimitOption = Annotated[
    Optional[int],
    typer.Option(
        "--limit",
        "-n",
        help="Process at most N tables / candidates.",
        show_default=False,
    ),
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = frozenset(
    {"password", "password_secret_ref", "auth_token", "secret", "token"}
)


def _redact_value(key: str, value: object) -> object:
    """Return '***' if *key* looks like a secret field, else *value*."""
    key_lower = key.lower()
    if any(s in key_lower for s in _SENSITIVE_KEYS):
        return "***"
    return value


def _redacted_config_summary(config: object) -> dict:
    """
    Build a flat dict representation of *config* with secrets replaced by
    '***'.  Walks one level of nested pydantic models.
    """
    summary: dict = {}
    try:
        top = config.model_dump() if hasattr(config, "model_dump") else vars(config)
    except Exception:
        return {"config": str(config)}

    for section_key, section_val in top.items():
        if isinstance(section_val, dict):
            summary[section_key] = {
                k: _redact_value(k, v) for k, v in section_val.items()
            }
        elif hasattr(section_val, "model_dump"):
            summary[section_key] = {
                k: _redact_value(k, v)
                for k, v in section_val.model_dump().items()
            }
        else:
            summary[section_key] = _redact_value(section_key, section_val)

    return summary


def _default_config_path() -> Path | None:
    """
    Search default config locations in priority order.

    1. ./config/default.yaml  (project-local)
    2. $XDG_CONFIG_HOME/discovery/default.yaml
    3. ~/.config/discovery/default.yaml  (fallback if XDG not set)
    """
    candidates = [
        Path("config") / "default.yaml",
    ]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "discovery" / "default.yaml")
    candidates.append(Path.home() / ".config" / "discovery" / "default.yaml")

    for p in candidates:
        if p.exists():
            return p
    return None


def _load_config(config_path: Optional[Path]):
    """Load config from *config_path* or the first discovered default path."""
    from discovery import config as config_module  # noqa: PLC0415

    path = config_path or _default_config_path()
    return config_module.load_config(path)


def _configure_logging(log_level: str, json_logs: bool) -> None:
    from discovery.logging_setup import configure_logging  # noqa: PLC0415

    configure_logging(level=log_level, json_output=json_logs)


def _build_engine(config):
    """Create a SQLAlchemy engine from config.results_db via results_db.get_engine."""
    from discovery.results_db import get_engine  # noqa: PLC0415

    return get_engine(config.results_db)


def _build_extraction_client(config):
    """Instantiate an ExtractionClient from config.extraction_service."""
    from discovery.extraction_client import ExtractionClient  # noqa: PLC0415

    svc = config.extraction_service
    return ExtractionClient(
        base_url=svc.base_url,
        auth_token=svc.auth_token,
        request_timeout_seconds=svc.request_timeout_seconds,
    )


def _abort(message: str, exit_code: int = 1) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=exit_code)


# ---------------------------------------------------------------------------
# discovery init
# ---------------------------------------------------------------------------


@app.command("init")
def cmd_init(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """
    Create the results schema and verify connectivity to Postgres and the
    extraction service.
    """
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: init results schema, test Postgres + extraction service.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        log.info("config_loaded", summary=_redacted_config_summary(config))

        from discovery import results_db  # noqa: PLC0415

        engine = _build_engine(config)

        # Locate results_schema.sql — it lives at pipeline/sql/results_schema.sql.
        # cli.py is at pipeline/src/discovery/cli.py, so three .parent calls reach
        # pipeline/, then descend into sql/.
        schema_sql_path = (
            Path(__file__).parent.parent.parent / "sql" / "results_schema.sql"
        )
        results_db.init_schema(engine, schema_sql_path)
        typer.secho("Results schema initialised.", fg=typer.colors.GREEN)

        client = _build_extraction_client(config)
        client.test_connection(config.source_db.to_connection_config())
        typer.secho("Extraction service reachable.", fg=typer.colors.GREEN)

        log.info("init_complete")
    except Exception as exc:
        log.error("init_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery inventory
# ---------------------------------------------------------------------------


@app.command("inventory")
def cmd_inventory(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 1: inventory the source database via the extraction service."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: run Phase 1 (inventory) via extraction service.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)
        client = _build_extraction_client(config)

        from discovery import inventory  # noqa: PLC0415

        inventory.run_phase_1(engine, client, config)
        typer.secho("Phase 1 (inventory) complete.", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("inventory_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery extract
# ---------------------------------------------------------------------------


@app.command("extract")
def cmd_extract(
    limit: LimitOption = None,
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 2: extract source tables to Parquet via the extraction service."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        limit_msg = f" (limit={limit})" if limit else ""
        typer.secho(
            f"[dry-run] Would: run Phase 2 (extraction){limit_msg}.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)
        client = _build_extraction_client(config)

        from discovery import extraction  # noqa: PLC0415

        extraction.run_phase_2(engine, client, config, limit=limit)
        typer.secho("Phase 2 (extraction) complete.", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("extract_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery fingerprint
# ---------------------------------------------------------------------------


@app.command("fingerprint")
def cmd_fingerprint(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 3a: compute column fingerprints (HyperMinHash + xxh3)."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: run Phase 3a (fingerprint).",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import fingerprint  # noqa: PLC0415

        fingerprint.run_phase_3a(engine, config)
        typer.secho("Phase 3a (fingerprint) complete.", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("fingerprint_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery pii-scan
# ---------------------------------------------------------------------------


@app.command("pii-scan")
def cmd_pii_scan(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 3b: scan columns for PII using Hyperscan + validators."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: run Phase 3b (PII scan).",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import pii_scan  # noqa: PLC0415

        pii_scan.run_phase_3b(engine, config)
        typer.secho("Phase 3b (PII scan) complete.", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("pii_scan_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery generate-candidates
# ---------------------------------------------------------------------------


@app.command("generate-candidates")
def cmd_generate_candidates(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 4: SQL pre-filter + FAISS LSH to generate FK candidates."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: run Phase 4 (generate candidates).",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import candidates  # noqa: PLC0415

        candidates.run_phase_4(engine, config)
        typer.secho("Phase 4 (generate candidates) complete.", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("generate_candidates_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery validate
# ---------------------------------------------------------------------------


@app.command("validate")
def cmd_validate(
    limit: LimitOption = None,
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 5: validate FK candidates using DuckDB joins on local Parquet."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        limit_msg = f" (limit={limit})" if limit else ""
        typer.secho(
            f"[dry-run] Would: run Phase 5 (validate){limit_msg}.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import validate  # noqa: PLC0415

        validate.run_phase_5(
            engine,
            config,
            parquet_dir=config.storage.base_path,
            limit=limit,
        )
        typer.secho("Phase 5 (validate) complete.", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("validate_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery composite-candidates  (Phase 4b -- composite/multi-column FKs)
# ---------------------------------------------------------------------------


@app.command("composite-candidates")
def cmd_composite_candidates(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 4b: detect composite (multi-column) FKs from singles."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: run Phase 4b (composite FKs).",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import composite_fk  # noqa: PLC0415

        n = composite_fk.run_phase_4b_composite(engine, config)
        typer.secho(
            f"Phase 4b complete -- {n} composite FK rows persisted.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:
        log.error("composite_candidates_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery polymorphic-candidates  (Phase 4c -- Rails-style polymorphic FK)
# ---------------------------------------------------------------------------


@app.command("polymorphic-candidates")
def cmd_polymorphic_candidates(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 4c: detect polymorphic FKs (commentable_type/_id pattern)."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: run Phase 4c (polymorphic FKs).",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import polymorphic_fk  # noqa: PLC0415

        n = polymorphic_fk.run_phase_polymorphic_fk(engine, config)
        typer.secho(
            f"Phase 4c complete -- {n} polymorphic FK rows persisted.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:
        log.error("polymorphic_candidates_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery jsonb-candidates  (Phase 4d -- JSONB soft FK)
# ---------------------------------------------------------------------------


@app.command("jsonb-candidates")
def cmd_jsonb_candidates(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Run Phase 4d: detect FK-shaped values inside JSONB columns."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: run Phase 4d (JSONB soft FKs).",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import jsonb_fk  # noqa: PLC0415

        n = jsonb_fk.run_phase_jsonb_fk(engine, config)
        typer.secho(
            f"Phase 4d complete -- {n} JSONB FK rows persisted.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:
        log.error("jsonb_candidates_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery annotate-inheritance  (post-Phase 5 evidence merge)
# ---------------------------------------------------------------------------


@app.command("annotate-inheritance")
def cmd_annotate_inheritance(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Tag PK<->PK 1.0-containment relationships with is_a_inheritance."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: tag inheritance relationships.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import inheritance  # noqa: PLC0415

        n = inheritance.run_phase_inheritance(engine, config)
        typer.secho(
            f"Inheritance annotator complete -- {n} relationships tagged.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:
        log.error("annotate_inheritance_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery propagate-pii  (post-Phase 5 subject-rooted reverse-BFS)
# ---------------------------------------------------------------------------


@app.command("propagate-pii")
def cmd_propagate_pii(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Tag every table reachable from a PII root with subject_kinds + distance."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: reverse-BFS PII roots, tag tbl_inventory.subject_kinds.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import pii_propagation  # noqa: PLC0415

        result = pii_propagation.run_phase_pii_propagation(engine, config)
        typer.secho(
            f"PII propagation complete -- {result['tables_tagged']} tables tagged "
            f"from {result['roots_seeded']} roots, max distance {result['max_distance']}.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:
        log.error("propagate_pii_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery leak-scan  (cross-cluster sketch-based PII leak detection)
# ---------------------------------------------------------------------------


@app.command("leak-scan")
def cmd_leak_scan(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Detect cross-table PII value-set overlaps using existing sketches."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: scan PII columns vs non-PII for sketch containment >= 0.5.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import pii_leak  # noqa: PLC0415

        result = pii_leak.run_phase_pii_leak(engine, config)
        typer.secho(
            f"PII leak scan complete -- {result['leaks']} leaks across "
            f"{result['sources']} PII source columns.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:
        log.error("leak_scan_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery cluster  (clustering phase — groups tables by schema)
# ---------------------------------------------------------------------------


@app.command("cluster")
def cmd_cluster(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Cluster tables within each schema and persist results to the clusters table."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: cluster tables per schema, persist to clusters table.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery.orchestrator import run_phase_clustering  # noqa: PLC0415

        result = run_phase_clustering(engine, config)
        typer.secho(
            f"Clustering complete -- {result['clusters_total']} clusters across "
            f"{result['schemas_processed']} schemas, "
            f"{result['junctions_collapsed']} junctions collapsed.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:
        log.error("cluster_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery report  (sub-app)
# ---------------------------------------------------------------------------


@report_app.command("relationships")
def cmd_report_relationships(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Generate relationships.csv + relationships.xlsx."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: write relationships.csv + relationships.xlsx.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from pathlib import Path as _Path  # noqa: PLC0415
        from discovery import report  # noqa: PLC0415

        out_dir = _Path(config.reporting.output_dir)
        paths = report.report_relationships(engine, out_dir)
        for p in paths:
            typer.secho(f"  Written: {p}", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("report_relationships_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


@report_app.command("pii")
def cmd_report_pii(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Generate pii_findings.csv + pii_findings.xlsx."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: write pii_findings.csv + pii_findings.xlsx.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from pathlib import Path as _Path  # noqa: PLC0415
        from discovery import report  # noqa: PLC0415

        out_dir = _Path(config.reporting.output_dir)
        paths = report.report_pii(engine, out_dir)
        for p in paths:
            typer.secho(f"  Written: {p}", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("report_pii_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


@report_app.command("exclusions")
def cmd_report_exclusions(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Generate exclusions.csv + exclusions.xlsx."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: write exclusions.csv + exclusions.xlsx.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from pathlib import Path as _Path  # noqa: PLC0415
        from discovery import report  # noqa: PLC0415

        out_dir = _Path(config.reporting.output_dir)
        paths = report.report_exclusions(engine, out_dir)
        for p in paths:
            typer.secho(f"  Written: {p}", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("report_exclusions_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


@report_app.command("all")
def cmd_report_all(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Generate all Phase 7 reports (relationships, PII, exclusions, summary)."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: write all Phase 7 reports.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)
        engine = _build_engine(config)

        from discovery import report  # noqa: PLC0415

        paths = report.generate_all(engine, config)
        for p in paths:
            typer.secho(f"  Written: {p}", fg=typer.colors.GREEN)
        typer.secho(f"Phase 7 (report all): {len(paths)} files.", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("report_all_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery run-all
# ---------------------------------------------------------------------------


@app.command("run-all")
def cmd_run_all(
    limit: LimitOption = None,
    skip: Annotated[
        Optional[str],
        typer.Option(
            "--skip",
            help=(
                "Comma-separated list of phases to skip unconditionally. "
                "Valid values: inventory, extract, fingerprint, pii_scan, "
                "candidate_gen, validate, report."
            ),
        ),
    ] = None,
    two_pass: Annotated[
        bool,
        typer.Option(
            "--two-pass/--no-two-pass",
            help=(
                "Two-pass extraction: triage on a small sample, then "
                "full-extract only the tables touched by surviving FK "
                "candidates.  Off by default (current full-extraction "
                "behaviour)."
            ),
        ),
    ] = False,
    sample_pct: Annotated[
        float,
        typer.Option(
            "--sample-pct",
            help=(
                "Per-table Bernoulli sampling percentage for Phase 2a "
                "(only used with --two-pass). 1.0 means 1%; 5.0 means 5%."
            ),
            min=0.0,
            max=100.0,
        ),
    ] = 1.0,
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """
    Run all phases (1 → 7) in order.  Fully resumable — already-complete
    phases are skipped automatically.

    With ``--two-pass``, Phase 2 runs a sample extract first (TABLESAMPLE
    BERNOULLI), Phases 3a/3b/4 produce candidate FKs from the sample, then
    Phase 2 re-runs in ``mode='full_subset'`` for tables touched by surviving
    candidates only — typically 5-15% of tables.  Phase 5 always validates
    on the resulting full data for surviving pairs.
    """
    _configure_logging(log_level, json_logs)

    skip_phases = [s.strip() for s in skip.split(",")] if skip else []

    if dry_run:
        limit_msg = f" limit={limit}" if limit else ""
        skip_msg = f" skip={skip_phases}" if skip_phases else ""
        mode_msg = (
            f" two-pass sample_pct={sample_pct}"
            if two_pass
            else " full-extract"
        )
        typer.secho(
            f"[dry-run] Would: run all phases (1→7){limit_msg}{skip_msg}{mode_msg}.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)

        if two_pass:
            from discovery.orchestrator import run_all_two_pass  # noqa: PLC0415

            run_all_two_pass(
                config,
                sample_pct=sample_pct,
                skip_phases=skip_phases,
            )
        else:
            from discovery.orchestrator import run_all  # noqa: PLC0415

            run_all(config, limit=limit, skip_phases=skip_phases)
        typer.secho("run-all complete.", fg=typer.colors.GREEN)
    except Exception as exc:
        log.error("run_all_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery status
# ---------------------------------------------------------------------------


@app.command("status")
def cmd_status(
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """Print per-phase progress from the run_log table."""
    _configure_logging(log_level, json_logs)

    if dry_run:
        typer.secho(
            "[dry-run] Would: query run_log and print per-phase progress.",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        config = _load_config(config_path)

        from discovery.run_log import RunLog  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        engine = _build_engine(config)

        # Query all run_log rows grouped by phase + status
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT phase, status, count(*) AS cnt "
                    "FROM discovery.run_log "
                    "GROUP BY phase, status "
                    "ORDER BY phase, status"
                )
            ).fetchall()

        if not rows:
            typer.secho("No runs recorded yet.", fg=typer.colors.YELLOW)
            return

        col_w = 20
        typer.secho(
            f"{'Phase':<{col_w}} {'Status':<12} {'Count':>6}",
            fg=typer.colors.BRIGHT_WHITE,
            bold=True,
        )
        typer.secho("-" * (col_w + 12 + 7))
        for row in rows:
            phase, status, count = row[0], row[1], row[2]
            color = {
                "succeeded": typer.colors.GREEN,
                "failed": typer.colors.RED,
                "started": typer.colors.YELLOW,
                "skipped": typer.colors.BRIGHT_BLACK,
            }.get(status, typer.colors.WHITE)
            typer.secho(
                f"{phase:<{col_w}} {status:<12} {count:>6}",
                fg=color,
            )
    except Exception as exc:
        log.error("status_failed", error=str(exc), exc_info=True)
        _abort(str(exc))


# ---------------------------------------------------------------------------
# discovery cleanup
# ---------------------------------------------------------------------------


@app.command("cleanup")
def cmd_cleanup(
    keep_results: Annotated[
        bool,
        typer.Option(
            "--keep-results/--no-keep-results",
            help="Preserve the results Postgres database (default: ON).",
        ),
    ] = True,
    drop_results: Annotated[
        bool,
        typer.Option(
            "--drop-results",
            help=(
                "Explicitly drop the results DB schema.  "
                "Required when combined with --purge to also delete results."
            ),
        ),
    ] = False,
    purge: Annotated[
        bool,
        typer.Option(
            "--purge",
            help=(
                "Nuclear option: rmtree the entire Parquet directory "
                "instead of running selective orphan GC.  Combine with "
                "--drop-results / --no-keep-results to also wipe the DB."
            ),
        ),
    ] = False,
    config_path: ConfigOption = None,
    dry_run: DryRunOption = False,
    log_level: LogLevelOption = "INFO",
    json_logs: Annotated[bool, typer.Option("--json-logs/--text-logs")] = True,
) -> None:
    """
    Selective Parquet garbage collection.

    Default behaviour: delete Parquet files for tables that no surviving
    fk_candidate references (status='extracted' AND not referenced as
    child or parent in fk_candidates).  Survivors and other in-use files
    are preserved.

    With ``--purge``: rmtree the entire Parquet directory (legacy behaviour).
    With ``--no-keep-results`` or ``--drop-results``: also drop the results
    DB schema (only in combination with --purge does this make sense; for
    selective GC the schema is required to identify orphans).
    """
    _configure_logging(log_level, json_logs)

    # Either --drop-results explicitly, or --no-keep-results, drops the schema.
    drop_db = drop_results or (not keep_results)

    if dry_run:
        if purge:
            db_msg = "DROP" if drop_db else "preserve"
            typer.secho(
                f"[dry-run] Would: rmtree Parquet directory; {db_msg} results DB.",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho(
                "[dry-run] Would: scan fk_candidates and delete orphaned Parquet files.",
                fg=typer.colors.YELLOW,
            )

            try:
                config = _load_config(config_path)
            except Exception:
                # Bail to avoid touching DB during dry-run if config is missing.
                return

            try:
                engine = _build_engine(config)
                from discovery.cleanup import (  # noqa: PLC0415
                    current_parquet_bytes,
                    gc_orphaned_parquet,
                )

                used = current_parquet_bytes(config)
                paths = gc_orphaned_parquet(engine, config, dry_run=True)
                typer.secho(
                    f"  Current Parquet bytes: {used:,}",
                    fg=typer.colors.YELLOW,
                )
                typer.secho(
                    f"  Would delete {len(paths)} files.",
                    fg=typer.colors.YELLOW,
                )
                for p in paths:
                    typer.secho(f"    - {p}", fg=typer.colors.YELLOW)
            except Exception as exc:
                log.warning("cleanup_dry_run_db_unavailable", error=str(exc))
                typer.secho(
                    f"  (DB unavailable for orphan scan: {exc})",
                    fg=typer.colors.YELLOW,
                )
        return

    try:
        config = _load_config(config_path)
        parquet_dir = Path(config.storage.base_path)

        if purge:
            import shutil  # noqa: PLC0415

            if parquet_dir.exists():
                shutil.rmtree(parquet_dir)
                typer.secho(
                    f"Removed Parquet directory: {parquet_dir}",
                    fg=typer.colors.GREEN,
                )
                log.info("cleanup_parquet_removed", path=str(parquet_dir))
            else:
                typer.secho(
                    f"Parquet directory not found: {parquet_dir}",
                    fg=typer.colors.YELLOW,
                )

            if drop_db:
                engine = _build_engine(config)
                from sqlalchemy import text  # noqa: PLC0415

                with engine.connect() as conn:
                    conn.execute(text("DROP SCHEMA IF EXISTS discovery CASCADE"))
                    conn.commit()
                typer.secho("Results schema dropped.", fg=typer.colors.GREEN)
                log.info("cleanup_results_db_dropped")
            else:
                typer.secho(
                    "Results DB preserved (default).",
                    fg=typer.colors.YELLOW,
                )
            return

        # Selective orphan GC (default path).
        engine = _build_engine(config)
        from discovery.cleanup import (  # noqa: PLC0415
            current_parquet_bytes,
            gc_orphaned_parquet,
        )

        before = current_parquet_bytes(config)
        deleted = gc_orphaned_parquet(engine, config, dry_run=False)
        after = current_parquet_bytes(config)

        typer.secho(
            f"Deleted {len(deleted)} orphaned Parquet files "
            f"({before - after:,} bytes freed; {after:,} remaining).",
            fg=typer.colors.GREEN,
        )
        log.info(
            "cleanup_gc_complete",
            deleted_count=len(deleted),
            bytes_freed=before - after,
            bytes_remaining=after,
        )
    except Exception as exc:
        log.error("cleanup_failed", error=str(exc), exc_info=True)
        _abort(str(exc))
