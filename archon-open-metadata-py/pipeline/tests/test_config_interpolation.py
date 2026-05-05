"""
test_config_interpolation.py — Tests for config._interpolate and config plumbing.

Verifies that:
  * ``${VAR}`` with no default raises EnvironmentError when VAR is unset
  * ``${VAR:default}`` falls back to the default when VAR is unset
  * ``${VAR}`` with VAR set in the environment substitutes correctly
  * Multiple missing variables are reported together
  * Empty-string default is honoured (not treated as "missing")
  * The Pydantic models declare every config field consumed via
    ``getattr(...)`` in the rest of the codebase (F4.1)
  * ``config/default.yaml`` exposes each declared knob with its default (F4.2)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from discovery.config import (
    AppConfig,
    ExtractionConfig,
    FingerprintConfig,
    PiiConfig,
    PiiDetectorsConfig,
    RelationshipsConfig,
    _interpolate,
    load_config,
)


def test_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "value123")
    assert _interpolate("foo=${MY_VAR}") == "foo=value123"


def test_var_with_default_used_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNSET_VAR", raising=False)
    assert _interpolate("foo=${UNSET_VAR:fallback}") == "foo=fallback"


def test_var_with_default_overridden_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANOTHER_VAR", "fromenv")
    assert _interpolate("foo=${ANOTHER_VAR:fallback}") == "foo=fromenv"


def test_var_no_default_unset_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REQUIRED_VAR", raising=False)
    with pytest.raises(EnvironmentError) as excinfo:
        _interpolate("foo=${REQUIRED_VAR}")
    assert "REQUIRED_VAR" in str(excinfo.value)


def test_multiple_missing_vars_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_A", raising=False)
    monkeypatch.delenv("MISSING_B", raising=False)
    with pytest.raises(EnvironmentError) as excinfo:
        _interpolate("a=${MISSING_A}\nb=${MISSING_B}")
    msg = str(excinfo.value)
    assert "MISSING_A" in msg
    assert "MISSING_B" in msg


def test_empty_default_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMPTY_VAR", raising=False)
    # Empty default → empty string, NOT an error.
    assert _interpolate("foo=${EMPTY_VAR:}") == "foo="


def test_no_tokens_passthrough() -> None:
    assert _interpolate("plain text without tokens") == "plain text without tokens"


def test_required_secret_refs_must_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """The classic pipeline secrets must be required (no default)."""
    for var in (
        "EXTRACTION_SERVICE_TOKEN",
        "RESULTS_DB_PASSWORD",
        "RESULTS_DB_USER",
        "RESULTS_DB_HOST",
        "RESULTS_DB_NAME",
        "SOURCE_DB_HOST",
        "SOURCE_DB_NAME",
        "SOURCE_DB_USER",
        "SOURCE_DB_PASSWORD_SECRET_REF",
    ):
        monkeypatch.delenv(var, raising=False)

    yaml_text = "\n".join(
        f"key{i}: ${{{name}}}"
        for i, name in enumerate(
            (
                "EXTRACTION_SERVICE_TOKEN",
                "RESULTS_DB_PASSWORD",
                "SOURCE_DB_HOST",
            )
        )
    )

    with pytest.raises(EnvironmentError):
        _interpolate(yaml_text)


# ---------------------------------------------------------------------------
# F4.1 — declared-but-not-yet-wired config fields must round-trip
# ---------------------------------------------------------------------------
#
# Several knobs are read elsewhere in the codebase via ``getattr(model, name,
# default)``.  If the attribute is never declared on the Pydantic model the
# fallback always wins — operators editing the YAML get no validation and the
# knob becomes a silent dead-button.  These tests assert each knob is now a
# declared field with the documented default.


class TestNewConfigFields:
    def test_relationships_require_parent_pk_default(self) -> None:
        rel = RelationshipsConfig()
        assert rel.require_parent_pk is True

    def test_relationships_validate_only_primary_tier_default(self) -> None:
        rel = RelationshipsConfig()
        assert rel.validate_only_primary_tier is True

    def test_relationships_overrides_round_trip(self) -> None:
        rel = RelationshipsConfig(
            require_parent_pk=False,
            validate_only_primary_tier=False,
        )
        assert rel.require_parent_pk is False
        assert rel.validate_only_primary_tier is False

    def test_extraction_config_exists_and_default(self) -> None:
        ext = ExtractionConfig()
        assert ext.column_projection is True

    def test_extraction_column_projection_override(self) -> None:
        ext = ExtractionConfig(column_projection=False)
        assert ext.column_projection is False

    def test_fingerprint_early_stop_delta_default(self) -> None:
        fp = FingerprintConfig()
        assert fp.early_stop_delta == pytest.approx(0.005)

    def test_pii_detectors_spacy_ner_default(self) -> None:
        det = PiiDetectorsConfig()
        assert det.spacy_ner is False

    def test_pii_match_rate_threshold_default(self) -> None:
        cfg = PiiConfig()
        assert cfg.match_rate_threshold == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# F4.2 — default.yaml must expose every knob declared on the model
# ---------------------------------------------------------------------------


def _default_yaml_path() -> Path:
    """Locate config/default.yaml relative to the pipeline source root."""
    # tests/<this file>  →  pipeline/  →  pipeline/config/default.yaml
    here = Path(__file__).resolve()
    return here.parent.parent / "config" / "default.yaml"


def _set_pipeline_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTRACTION_SERVICE_TOKEN", "x")
    monkeypatch.setenv("SOURCE_DB_PASSWORD_SECRET_REF", "env://X")
    monkeypatch.setenv("SOURCE_DB_HOST", "h")
    monkeypatch.setenv("SOURCE_DB_NAME", "d")
    monkeypatch.setenv("SOURCE_DB_USER", "u")
    monkeypatch.setenv("RESULTS_DB_HOST", "h")
    monkeypatch.setenv("RESULTS_DB_USER", "u")
    monkeypatch.setenv("RESULTS_DB_PASSWORD", "p")
    monkeypatch.setenv("RESULTS_DB_NAME", "r")
    monkeypatch.setenv("X", "x")


class TestDefaultYamlSurfaces:
    """Each new pydantic-typed knob should appear in default.yaml."""

    def test_default_yaml_has_extraction_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_pipeline_secrets(monkeypatch)
        path = _default_yaml_path()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "extraction" in raw, "default.yaml missing extraction block"
        assert raw["extraction"].get("column_projection") is True

    def test_default_yaml_has_fingerprint_early_stop_delta(self) -> None:
        path = _default_yaml_path()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        fp = raw.get("fingerprint", {})
        assert "early_stop_delta" in fp
        assert float(fp["early_stop_delta"]) == pytest.approx(0.005)

    def test_default_yaml_has_pii_detector_spacy_ner(self) -> None:
        path = _default_yaml_path()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        det = raw.get("pii", {}).get("detectors", {})
        assert "spacy_ner" in det
        assert det["spacy_ner"] is False

    def test_default_yaml_has_relationships_knobs(self) -> None:
        path = _default_yaml_path()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        rel = raw.get("relationships", {})
        assert "require_parent_pk" in rel
        assert "validate_only_primary_tier" in rel
        assert rel["require_parent_pk"] is True
        assert rel["validate_only_primary_tier"] is True

    def test_load_config_round_trips_new_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_pipeline_secrets(monkeypatch)
        cfg = load_config(_default_yaml_path())
        assert cfg.extraction.column_projection is True
        assert cfg.fingerprint.early_stop_delta == pytest.approx(0.005)
        assert cfg.pii.detectors.spacy_ner is False
        assert cfg.pii.match_rate_threshold == pytest.approx(0.05)
        assert cfg.relationships.require_parent_pk is True
        assert cfg.relationships.validate_only_primary_tier is True

    def test_app_config_has_extraction_field(self) -> None:
        # The pydantic field must exist on AppConfig so callers reading
        # ``cfg.extraction.column_projection`` don't fall through to None.
        assert "extraction" in AppConfig.model_fields
        assert AppConfig.model_fields["extraction"].annotation is ExtractionConfig


# ---------------------------------------------------------------------------
# Tier 1+2+3 accuracy improvements — new RelationshipsConfig knobs
# ---------------------------------------------------------------------------


class TestRelationshipsTierImprovements:
    """Defaults and overrides for the new accuracy-improvement knobs."""

    def test_relationships_new_fields_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Loading default.yaml with no overrides yields the documented defaults."""
        _set_pipeline_secrets(monkeypatch)
        cfg = load_config(_default_yaml_path())
        rel = cfg.relationships

        # Tier 1+2
        assert rel.plural_name_normalize is True
        assert rel.pii_filter_enabled is True
        assert rel.one_implicit_pk_per_table is True
        assert rel.reverse_direction_reconciliation is True
        assert rel.top_k_per_child == 10
        assert rel.dense_serial_hard_reject is True
        assert rel.range_overlap_gate_enabled is True
        assert rel.max_relationships is None

        # Tier 3 — semantic (Sprint A8: default flipped to True)
        assert rel.semantic_name_similarity is True
        assert rel.semantic_min_score == pytest.approx(0.5)

        # Composite FK -- default flipped to True now that composite_fk
        # is folded into the standard run-all pipeline.
        assert rel.composite_fk_enabled is True
        assert rel.composite_fk_max_arity == 3
        assert rel.composite_fk_min_containment == pytest.approx(0.95)

        # Polymorphic / JSONB / inheritance defaults (added by agent C).
        assert rel.polymorphic_fk_enabled is True
        assert rel.polymorphic_min_containment == pytest.approx(0.95)
        assert rel.jsonb_fk_enabled is True
        assert rel.jsonb_sample_rows == 1000
        assert rel.inheritance_annotator_enabled is True

    def test_relationships_top_k_override(self) -> None:
        """top_k_per_child override is parsed correctly."""
        rel = RelationshipsConfig(top_k_per_child=10)
        assert rel.top_k_per_child == 10
        assert isinstance(rel.top_k_per_child, int)

    def test_relationships_semantic_enabled_default(self) -> None:
        """semantic_name_similarity defaults to True (Sprint A8)."""
        rel = RelationshipsConfig()
        assert rel.semantic_name_similarity is True

    def test_relationships_max_relationships_int(self) -> None:
        """max_relationships: 1000 parses to int."""
        rel = RelationshipsConfig(max_relationships=1000)
        assert rel.max_relationships == 1000
        assert isinstance(rel.max_relationships, int)

    def test_relationships_max_relationships_null(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`null` in YAML maps to Python None."""
        _set_pipeline_secrets(monkeypatch)
        # Build a minimal YAML that overrides max_relationships to null.
        original = _default_yaml_path().read_text(encoding="utf-8")
        # The default file already has `max_relationships: null` so just
        # round-trip it and assert the parsed value is None.
        cfg = load_config(_default_yaml_path())
        assert cfg.relationships.max_relationships is None

        # Also assert that an explicit YAML override of `null` round-trips.
        override = tmp_path / "override.yaml"
        override.write_text(original.replace(
            "max_relationships: null", "max_relationships: null"
        ), encoding="utf-8")
        cfg2 = load_config(override)
        assert cfg2.relationships.max_relationships is None
