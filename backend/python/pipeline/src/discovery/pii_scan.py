"""
Phase 3b — PII scanning.

Owns BOTH the pure scanner (PIIMatcher, scan_column, validators, redaction)
and the Phase 3b orchestrator (run_phase_3b).

Pure helpers have no SQLAlchemy / config / run_log imports.  The orchestrator
imports those at function-scope.

Exports
-------
PATTERN_DEFS               — re-exported from :mod:`pii_patterns`
PatternDef                 — re-exported
PIIMatcher
PIIFinding
luhn_valid / iban_valid / entropy_looks_random / ssn_us_valid / date_parseable
redact
scan_column
run_phase_3b

Upgrade highlights (vs. the original 8-pattern scanner)
-------------------------------------------------------
* Pattern catalog moved to :mod:`pii_patterns` (8 existing + 39 new).
* Span-overlap resolver in :mod:`pii_score` fixes the
  ``card_number_raw`` PHONE_US/CC_NUMBER false-positive.
* Column-name priors (:mod:`pii_priors`) surface "obvious-by-name" columns
  even when the regex match rate is below threshold.
* Locale-aware validators (:mod:`pii_locale`) plug in stdnum + phonenumbers.
* Bayesian scoring (:mod:`pii_score`) combines name / regex / validator
  signals into a single ``score`` field on each finding.
* Phase 3b resume filter — re-runs no longer re-scan succeeded columns.
* Logger migrated from stdlib ``logging`` to ``structlog``.
"""
from __future__ import annotations

import math
import multiprocessing
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

# Re-export the catalog so the legacy import surface keeps working:
#   from discovery.pii_scan import PATTERN_DEFS, PatternDef
from discovery.pii_patterns import (  # noqa: F401  (re-exports)
    PATTERN_DEFS,
    PATTERNS,
    PatternDef,
    SPECIFICITY,
    get_pattern,
)
from discovery.pii_priors import name_prior, name_prior_strength
from discovery.pii_score import Match as _ScoreMatch
from discovery.pii_score import column_pii_confidence, resolve_overlaps

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from discovery.config import AppConfig

log = structlog.get_logger("discovery.pii_scan")


# ---------------------------------------------------------------------------
# Type classes that should be scanned for PII
# ---------------------------------------------------------------------------
_TEXT_TYPE_CLASSES = {"STRING_SHORT", "STRING_LONG"}

_TEXT_ARROW_TYPES = frozenset(
    [pa.string(), pa.large_string(), pa.utf8(), pa.large_utf8()]
)


def _is_text_type(arrow_type: pa.DataType, type_class: Optional[str] = None) -> bool:
    """Return True if the column should be PII-scanned."""
    if type_class in _TEXT_TYPE_CLASSES:
        return True
    if arrow_type in _TEXT_ARROW_TYPES:
        return True
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return True
    return False


# ---------------------------------------------------------------------------
# Validators (legacy + locale-aware)
# ---------------------------------------------------------------------------


def luhn_valid(number_str: str) -> bool:
    """Luhn checksum — returns True for valid credit card numbers."""
    digits = [int(c) for c in re.sub(r"\D", "", number_str)]
    if len(digits) < 13:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# Resolve stdnum.iban once at import; tolerate missing dependency.
try:
    from stdnum import iban as _stdnum_iban  # type: ignore[import]
    _IBAN_VALIDATOR: Optional[Callable[[str], bool]] = _stdnum_iban.is_valid
except ImportError:
    _IBAN_VALIDATOR = None


def iban_valid(value: str) -> bool:
    """Validate IBAN via python-stdnum.

    Returns True if stdnum is unavailable (don't false-negative on missing lib).
    Returns False on a real validation failure or any unexpected exception.
    """
    if _IBAN_VALIDATOR is None:
        return True
    try:
        return bool(_IBAN_VALIDATOR(value))
    except Exception as exc:
        log.warning("iban_valid_error", exc_type=type(exc).__name__)
        return False


def entropy_looks_random(s: str) -> bool:
    """Shannon entropy >= 3.5 bits/char and len >= 20 → probably API key/secret."""
    if len(s) < 20:
        return False
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    total = len(s)
    ent = -sum((cnt / total) * math.log2(cnt / total) for cnt in freq.values())
    return ent >= 3.5


def ssn_us_valid(value: str) -> bool:
    """SSN validation — the regex already rejects 000-, 666-, 900-999 areas."""
    return True


def date_parseable(value: str) -> bool:
    """Return True if value is a parseable date (DOB validator)."""
    from datetime import datetime as _dt

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            _dt.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


# Legacy validator dispatch — names appearing on the original 8 patterns.
_LEGACY_VALIDATORS: dict[str, Callable[[str], bool]] = {
    "luhn": luhn_valid,
    "iban": iban_valid,
    "entropy": entropy_looks_random,
    "ssn_us": ssn_us_valid,
    "dob": date_parseable,
}

# Subset of legacy validators that are NOT stdnum-backed.  These bypass the
# ``stdnum_validators`` allowlist gate so operators who omit them from their
# allowlist do not accidentally lose Luhn / entropy / SSN / DOB validation.
# ``iban`` is intentionally excluded — it is stdnum-backed and the allowlist
# should gate it.
_NON_STDNUM_LEGACY: frozenset[str] = frozenset({"luhn", "entropy", "ssn_us", "dob"})


# Patterns whose regex collapses to "any N-digit string" — without their
# stdnum module installed, every numeric column would be flagged.  When the
# matching validator is in :data:`pii_locale.LOCALE_VALIDATORS_FALLBACK_DENY`
# AND the stdnum module is missing, the validator hard-fails (returns False)
# rather than the usual permissive True.  See F4.4 in the post-impl review.
_HIGH_FP_PII_TYPES: frozenset[str] = frozenset(
    {"PASSPORT_GB", "BSN_NL", "PESEL_PL", "TAX_ID_DE", "NPI_US"}
)


def _validate(
    pii_type: str,
    value: str,
    validator_name: Optional[str],
    *,
    detector_toggles: Optional[dict] = None,
) -> bool:
    """Dispatch validator by symbolic name.

    Resolution order:
      1. Legacy (``luhn``, ``iban``, ``entropy``, ``ssn_us``, ``dob``).
      2. Locale-aware (``pii_locale.LOCALE_VALIDATORS``).
      3. Permissive ``True`` if the name is unknown.

    The optional ``detector_toggles`` mapping lets callers thread
    ``PiiDetectorsConfig`` knobs through the worker boundary:

    * ``luhn_validation`` — when False, skip the Luhn check (the validator
      effectively returns True so the regex remains authoritative).
    * ``stdnum_validators`` — when present, only the validators whose names
      appear in the list are run; other locale validators short-circuit to
      True (legacy permissive behaviour, no behavioural regression).

    A ``None`` ``validator_name`` paired with a high-FP pattern (see
    :data:`_HIGH_FP_PII_TYPES`) hard-fails to ``False`` — without a checksum
    the regex matches every numeric string of the right length.
    """
    if validator_name is None:
        # F4.4 guard: PASSPORT_GB and similar patterns whose regex is "any
        # N-digit string" must not pass without a validator.  If no validator
        # is configured AND the pii_type is high-FP, deny rather than accept.
        if pii_type in _HIGH_FP_PII_TYPES:
            return False
        return True

    # luhn_validation toggle — operator-overridable bypass.
    if (
        detector_toggles is not None
        and validator_name == "luhn"
        and detector_toggles.get("luhn_validation", True) is False
    ):
        return True

    # stdnum_validators allowlist — gates stdnum-backed validators, including
    # the ``iban`` entry in the legacy dispatch which delegates to
    # ``stdnum.iban``.  Non-stdnum legacy names (``luhn``, ``entropy``,
    # ``ssn_us``, ``dob``) bypass this gate so disabling them does not
    # accidentally turn off Luhn / entropy / SSN / DOB checks.  An empty list
    # means "run none of them" — distinct from ``None`` (allowlist disabled).
    if detector_toggles is not None and validator_name not in _NON_STDNUM_LEGACY:
        allow = detector_toggles.get("stdnum_validators")
        if allow is not None and validator_name not in allow:
            # Skip with permissive True (regex remains authoritative).
            return True

    fn = _LEGACY_VALIDATORS.get(validator_name)
    if fn is None:
        # Defer to the locale-aware table.
        from discovery.pii_locale import get_validator

        fn = get_validator(validator_name)
    if fn is None:
        # F4.4 hard-fail for high-FP patterns when stdnum module is missing.
        # See pii_locale.LOCALE_VALIDATORS_FALLBACK_DENY for the deny list.
        try:
            from discovery.pii_locale import LOCALE_VALIDATORS_FALLBACK_DENY
        except ImportError:
            LOCALE_VALIDATORS_FALLBACK_DENY = set()  # type: ignore[assignment]
        if (
            validator_name in LOCALE_VALIDATORS_FALLBACK_DENY
            or pii_type in _HIGH_FP_PII_TYPES
        ):
            return False
        return True
    try:
        return bool(fn(value))
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "validator_raised",
            validator=validator_name,
            pii_type=pii_type,
            exc=type(exc).__name__,
        )
        return False


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact(value: str, pii_type: str) -> str:
    """Return a privacy-safe token. NEVER leak raw PII."""
    if pii_type == "EMAIL":
        if "@" in value:
            local, domain = value.split("@", 1)
            tld = ("." + domain.split(".")[-1]) if "." in domain else ""
            domain_label = domain.split(".")[0] if "." in domain else domain
            # For very short locals or domain-labels, drop the first-char hint.
            if len(local) <= 2 or len(domain_label) <= 2:
                return f"***@***{tld}"
            return f"{local[:1]}***@{domain[:1]}***{tld}"
        return "***"

    if pii_type == "API_KEY" or pii_type in {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET",
        "GCP_API_KEY",
        "GH_PAT",
        "JWT",
        "PRIVATE_KEY_PEM",
    }:
        return "***"

    if pii_type in (
        "CC_NUMBER",
        "SSN_US",
        "IBAN",
        "ITIN_US",
        "AADHAAR_IN",
        "PAN_IN",
        "CPF_BR",
        "CURP_MX",
        "DNI_ES",
        "NIR_FR",
        "TAX_ID_DE",
        "PESEL_PL",
        "BSN_NL",
        "PERSONNUMMER_SE",
        "NRIC_SG",
        "NHS_GB",
        "NINO_GB",
        "NPI_US",
        "MEDICARE_MBI_US",
        "PASSPORT_US",
        "PASSPORT_GB",
        "PASSPORT_IN",
        "DL_US",
        "ABA_ROUTING_US",
        "SWIFT_BIC",
        "BANK_ACCOUNT",
        "VAT_EU",
        "CODICE_FISCALE_IT",
    ):
        stripped = re.sub(r"[^A-Za-z0-9]", "", value)
        tail = stripped[-4:] if len(stripped) >= 4 else stripped
        return f"***{tail}"

    if pii_type.startswith("PHONE") or pii_type == "PHONE":
        digits = re.sub(r"\D", "", value)
        return f"***{digits[-4:]}" if len(digits) >= 4 else "***"

    if pii_type == "DOB":
        m = re.match(r"((?:19|20)\d{2})", value)
        return f"{m.group(1)}-**-**" if m else "****-**-**"

    if pii_type in {"IPV4", "IPV6"}:
        return "***"

    if pii_type == "GEO_COORD":
        return "lat***,lon***"

    if pii_type == "POSTAL_CODE":
        return f"{value[:1]}***" if value else "***"

    if pii_type in {"NAME", "LOCATION", "ORG", "PERSON_NAME", "ADDRESS", "DATE_NER", "MRN", "ICD10"}:
        return f"{value[:1]}***" if value else "***"

    return f"{value[:1]}***" if value else "***"


# ---------------------------------------------------------------------------
# PIIFinding dataclass
# ---------------------------------------------------------------------------


@dataclass
class PIIFinding:
    """PII detection result for one (column, pii_type) pair."""

    parquet_path: str
    column: str
    pii_type: str
    detector: str  # 'hyperscan' | 'regex' | 'name_prior' | 'ner'
    match_count: int = 0
    validated_count: int = 0
    sample_count: int = 0
    redacted_examples: list[str] = field(default_factory=list)
    # Bayesian / metadata fields (populated by scan_column):
    name_prior: bool = False
    regex_match_rate: float = 0.0
    score: float = 0.0
    specificity: int = 0

    @property
    def match_rate(self) -> float:
        """Validated rate — fraction of scanned rows whose value passed validation."""
        return self.validated_count / max(self.sample_count, 1)


# ---------------------------------------------------------------------------
# PIIMatcher
# ---------------------------------------------------------------------------


class PIIMatcher:
    """
    Multi-pattern scanner.  Tries Hyperscan (SIMD) first; falls back to regex.

    Hyperscan Database/Scratch are not picklable — each worker process must
    construct its own instance.

    The :meth:`scan` method now returns ``(name, value, start, end)`` tuples
    so the overlap resolver can decide which match wins on a shared span.

    Parameters
    ----------
    use_hyperscan:
        If False, skip the Hyperscan probe entirely and use the regex
        fallback unconditionally — operator override of
        ``config.pii.detectors.hyperscan``.  Default ``True`` retains
        auto-detection behaviour for backwards compatibility.
    """

    def __init__(self, use_hyperscan: bool = True) -> None:
        self._use_hyperscan = False
        self._detector_label = "regex"
        if use_hyperscan:
            self._try_hyperscan()
        if not self._use_hyperscan:
            self._build_regex_fallback()

    def _try_hyperscan(self) -> None:
        try:
            import hyperscan as hs  # type: ignore[import]

            db = hs.Database()
            expressions = [p.regex_bytes for p in PATTERN_DEFS]
            ids = list(range(len(PATTERN_DEFS)))
            flags = [hs.HS_FLAG_SOM_LEFTMOST for _ in PATTERN_DEFS]
            db.compile(expressions=expressions, ids=ids, flags=flags)
            scratch = hs.Scratch(db)
            self._hs = hs
            self._db = db
            self._scratch = scratch
            self._use_hyperscan = True
            self._detector_label = "hyperscan"
        except Exception as exc:  # noqa: BLE001
            log.debug("hyperscan_init_failed", exc=str(exc))

    def _build_regex_fallback(self) -> None:
        compiled: list[tuple[str, re.Pattern[str], Optional[str]]] = []
        for p in PATTERN_DEFS:
            try:
                rx = re.compile(p.regex_bytes.decode("utf-8"))
            except re.error as exc:
                log.warning(
                    "regex_compile_failed",
                    pattern=p.name,
                    err=str(exc),
                )
                continue
            compiled.append((p.name, rx, p.validator))
        self._compiled = compiled

    @property
    def detector_label(self) -> str:
        return self._detector_label

    def scan(self, text: str) -> list[tuple[str, str, int, int]]:
        """Return ``(pattern_name, matched_value, start, end)`` for each hit."""
        if not text:
            return []
        if self._use_hyperscan:
            return self._scan_hyperscan(text)
        return self._scan_regex(text)

    def _scan_hyperscan(self, text: str) -> list[tuple[str, str, int, int]]:
        out: list[tuple[str, str, int, int]] = []
        data = text.encode("utf-8", errors="ignore")

        def on_match(pid: int, start: int, end: int, flags: int, ctx: object) -> None:
            matched = data[start:end].decode("utf-8", errors="ignore")
            out.append((PATTERN_DEFS[pid].name, matched, start, end))

        self._db.scan(data, match_event_handler=on_match, scratch=self._scratch)
        return out

    def _scan_regex(self, text: str) -> list[tuple[str, str, int, int]]:
        out: list[tuple[str, str, int, int]] = []
        for name, pat, _ in self._compiled:
            for m in pat.finditer(text):
                start, end = m.span()
                out.append((name, m.group(0), start, end))
        return out


# ---------------------------------------------------------------------------
# Column scanner
# ---------------------------------------------------------------------------


def scan_column(
    parquet_path: Path,
    column: str,
    matcher: PIIMatcher,
    max_rows: int = 50_000,
    max_examples: int = 3,
    type_class: Optional[str] = None,
    random_seed: int = 42,
    enable_ner: bool = False,
    detector_toggles: Optional[dict] = None,
) -> list[PIIFinding]:
    """Scan one column of a Parquet file for PII.

    Behaviour notes
    ---------------
    * Each scanned row's matches go through :func:`pii_score.resolve_overlaps`
      so that, e.g., a 16-digit Luhn-valid CC also matching ``PHONE_US`` on a
      sub-span surfaces only as ``CC_NUMBER``.
    * For each (column, pii_type) pair we track ``regex_match_count`` (every
      regex hit) **and** ``validated_count`` (validator-accepted hits).  The
      Bayesian score uses the ratios of these.
    * The column name is consulted via :func:`pii_priors.name_prior`.  If the
      name strongly implies a PII type but the regex match rate is below
      ``0.05``, we still emit a finding with ``name_prior=True`` so reviewers
      see the column.
    * If ``enable_ner=True`` and ``STRING_LONG``-class column, additional
      findings are produced via :mod:`pii_ner` (no-op when spaCy missing).
    """
    parquet_path = Path(parquet_path)

    pf = pq.ParquetFile(str(parquet_path))
    schema = pf.schema_arrow

    try:
        field_idx = schema.get_field_index(column)
        arrow_field = schema.field(field_idx)
    except (KeyError, ValueError):
        return []

    if not _is_text_type(arrow_field.type, type_class):
        return []

    total_rows = sum(
        pf.metadata.row_group(rg).num_rows for rg in range(pf.num_row_groups)
    )

    if total_rows == 0:
        return []

    stride = max(1, math.ceil(total_rows / max_rows))

    # Per-pattern accumulators
    regex_match_counts: dict[str, int] = {}
    validated_counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}

    # Optional NER accumulators (keyed by pii_type the entity maps to)
    ner_counts: dict[str, int] = {}
    ner_examples: dict[str, list[str]] = {}
    ner_active = enable_ner and (type_class == "STRING_LONG" or type_class is None)
    if ner_active:
        try:
            from discovery.pii_ner import AVAILABLE as _NER_AVAILABLE
            from discovery.pii_ner import scan_text as _ner_scan_text
        except ImportError:  # defensive — module is in this package
            _NER_AVAILABLE = False
            _ner_scan_text = None  # type: ignore[assignment]
    else:
        _NER_AVAILABLE = False
        _ner_scan_text = None  # type: ignore[assignment]

    global_row_idx = 0
    rows_scanned = 0

    for rg_idx in range(pf.num_row_groups):
        rg_rows = pf.metadata.row_group(rg_idx).num_rows
        batch = pf.read_row_group(rg_idx, columns=[column])
        col_list = batch.column(column).to_pylist()

        for local_idx, val in enumerate(col_list):
            row_num = global_row_idx + local_idx
            if row_num % stride != 0:
                continue
            rows_scanned += 1
            if rows_scanned > max_rows:
                break

            if not isinstance(val, str) or not val:
                continue

            raw = matcher.scan(val)

            # Build Match records with validation status, then resolve overlaps.
            score_matches: list[_ScoreMatch] = []
            for pii_type, matched, start, end in raw:
                pattern_def = get_pattern(pii_type)
                validator_name = pattern_def.validator if pattern_def else None
                is_valid = _validate(
                    pii_type,
                    matched,
                    validator_name,
                    detector_toggles=detector_toggles,
                )
                score_matches.append(
                    _ScoreMatch(
                        name=pii_type,
                        value=matched,
                        start=start,
                        end=end,
                        validated=is_valid,
                    )
                )

            # D1.1 multi-hit cap: each cell contributes at most 1 to the
            # match count per detector, regardless of how many disjoint regex
            # spans match.  Without this gate a movie title containing two
            # SWIFT_BIC-shaped tokens would inflate the per-column match rate
            # to >100% (we observed 278% for VAT_EU on movie titles).  The
            # cell-level bucket also caps validated_count and example
            # collection so a single noisy cell can't dominate the surface.
            seen_in_cell: set[str] = set()
            for m in resolve_overlaps(score_matches):
                if m.name in seen_in_cell:
                    continue
                seen_in_cell.add(m.name)
                regex_match_counts[m.name] = regex_match_counts.get(m.name, 0) + 1
                if m.validated:
                    validated_counts[m.name] = validated_counts.get(m.name, 0) + 1
                    bucket = examples.setdefault(m.name, [])
                    if len(bucket) < max_examples:
                        bucket.append(redact(m.value, m.name))

            # NER pass — only on STRING_LONG values when configured
            if _NER_AVAILABLE and _ner_scan_text is not None:
                for ent in _ner_scan_text(val):
                    ner_counts[ent.pii_type] = ner_counts.get(ent.pii_type, 0) + 1
                    bucket = ner_examples.setdefault(ent.pii_type, [])
                    if len(bucket) < max_examples:
                        bucket.append(redact(ent.text, ent.pii_type))

        global_row_idx += rg_rows
        if rows_scanned >= max_rows:
            break

    # ------------------------------------------------------------------
    # Materialise findings (regex/hyperscan + NER + name-prior surface)
    # ------------------------------------------------------------------
    findings: list[PIIFinding] = []

    # Compute name prior once — used by every finding plus the
    # "name-prior-only" surface case below.
    prior_type = name_prior(column)
    prior_strength = (
        name_prior_strength(column, prior_type) if prior_type else 0.0
    )

    seen_types: set[str] = set()

    # Regex / Hyperscan findings
    for pii_type, match_count in regex_match_counts.items():
        validated = validated_counts.get(pii_type, 0)
        rate_regex = match_count / max(rows_scanned, 1)
        rate_validated = validated / max(match_count, 1)
        np_strength = name_prior_strength(column, pii_type)
        score = column_pii_confidence(
            name_prior_strength=np_strength,
            regex_match_rate=rate_regex,
            validator_pass_rate=rate_validated,
        )
        spec = SPECIFICITY.get(pii_type, 50)
        findings.append(
            PIIFinding(
                parquet_path=str(parquet_path),
                column=column,
                pii_type=pii_type,
                detector=matcher.detector_label,
                match_count=match_count,
                validated_count=validated,
                sample_count=rows_scanned,
                redacted_examples=examples.get(pii_type, []),
                name_prior=(np_strength > 0.0),
                regex_match_rate=rate_regex,
                score=score,
                specificity=spec,
            )
        )
        seen_types.add(pii_type)

    # NER findings — emitted as a separate detector so they don't collide on
    # (column_id, pii_type, detector) with regex findings of the same type.
    for ner_type, count in ner_counts.items():
        rate_regex = count / max(rows_scanned, 1)
        np_strength = name_prior_strength(column, ner_type)
        score = column_pii_confidence(
            name_prior_strength=np_strength,
            regex_match_rate=rate_regex,
            validator_pass_rate=1.0,  # NER is its own validator
        )
        findings.append(
            PIIFinding(
                parquet_path=str(parquet_path),
                column=column,
                pii_type=ner_type,
                detector="ner",
                match_count=count,
                validated_count=count,
                sample_count=rows_scanned,
                redacted_examples=ner_examples.get(ner_type, []),
                name_prior=(np_strength > 0.0),
                regex_match_rate=rate_regex,
                score=score,
                specificity=50,
            )
        )
        seen_types.add(ner_type)

    # Name-prior-only surface — emit a finding even if the regex didn't fire.
    if (
        prior_type is not None
        and prior_type not in seen_types
        and prior_strength > 0.0
    ):
        rate_regex = regex_match_counts.get(prior_type, 0) / max(rows_scanned, 1)
        # Saturating Bayesian — π_match=0 and π_validate=0, so score equals
        # π_name (e.g. 0.85 for an exact key hit).
        score = column_pii_confidence(
            name_prior_strength=prior_strength,
            regex_match_rate=rate_regex,
            validator_pass_rate=0.0,
        )
        findings.append(
            PIIFinding(
                parquet_path=str(parquet_path),
                column=column,
                pii_type=prior_type,
                detector="name_prior",
                match_count=0,
                validated_count=0,
                sample_count=rows_scanned,
                redacted_examples=[],
                name_prior=True,
                regex_match_rate=rate_regex,
                score=score,
                specificity=SPECIFICITY.get(prior_type, 50),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Phase 3b worker pool
# ---------------------------------------------------------------------------

_worker_matcher: Any = None
_worker_settings: dict = {}


def _worker_init(settings: dict) -> None:
    """Initialise PIIMatcher once per worker process.

    The ``settings`` dict carries:

    * ``max_rows`` / ``max_examples`` — scan_column tuning
    * ``enable_ner`` — opt-in spaCy NER pass
    * ``detector_toggles`` — :class:`PiiDetectorsConfig` mapping with the
      ``hyperscan``, ``luhn_validation``, ``stdnum_validators`` knobs.
      The ``hyperscan`` flag is consumed at PIIMatcher construction here;
      the other two are consumed inside ``_validate`` and threaded through
      ``scan_column(..., detector_toggles=...)``.
    """
    global _worker_matcher, _worker_settings
    toggles = settings.get("detector_toggles") or {}
    use_hyperscan = bool(toggles.get("hyperscan", True))
    _worker_matcher = PIIMatcher(use_hyperscan=use_hyperscan)
    _worker_settings = settings
    log.debug(
        "pii_worker_ready",
        detector=_worker_matcher.detector_label,
    )


def _pii_task(args: tuple) -> list[dict] | None:
    """
    Picklable worker task.

    args: (column_id, parquet_path, column_name, type_class)
    Returns list of dicts ready for DB insertion.
    """
    column_id, parquet_path, column_name, type_class = args
    settings = _worker_settings

    try:
        findings = scan_column(
            parquet_path=Path(parquet_path),
            column=column_name,
            matcher=_worker_matcher,
            max_rows=settings.get("max_rows", 50_000),
            max_examples=settings.get("max_examples", 3),
            type_class=type_class,
            enable_ner=settings.get("enable_ner", False),
            detector_toggles=settings.get("detector_toggles"),
        )
        return [
            {
                "column_id": column_id,
                "pii_type": f.pii_type,
                "detector": f.detector,
                "match_count": f.match_count,
                "sample_count": f.sample_count,
                "match_rate": f.match_rate,
                "validated": f.validated_count > 0,
                "redacted_examples": f.redacted_examples,
                "regex_match_rate": f.regex_match_rate,
                "name_prior": f.name_prior,
                "score": f.score,
                "specificity": f.specificity,
            }
            for f in findings
        ]
    except Exception as exc:
        log.error(
            "pii_task_failed",
            column_id=column_id,
            parquet_path=str(parquet_path),
            column=column_name,
            exc=str(exc),
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Phase 3b entry point
# ---------------------------------------------------------------------------


_PERSIST_BATCH = 500


def run_phase_3b(engine: "Engine", config: "AppConfig") -> None:
    """
    Orchestrate Phase 3b: PII scan on all eligible text columns.

    Resumability
    ------------
    Columns whose latest ``run_log`` row for ``(phase='pii_scan',
    scope_type='column', scope_id=column_id)`` has ``status='succeeded'`` are
    skipped — fixes the audit's E2 finding ("Phase 3b has no resume filter").

    Persists findings in batches to avoid per-row transactions.
    """
    from sqlalchemy import and_, select

    from discovery.results_db import (
        PiiFinding,
        col_inventory_t,
        run_log_t,
        tbl_inventory_t,
        txn,
    )
    from discovery.run_log import RunLog

    run_log = RunLog(engine)

    # ----------------------------------------------------------
    # Banner: log phonenumbers / NER availability once at scan-start.
    # ----------------------------------------------------------
    try:
        from discovery.pii_locale import PHONENUMBERS_AVAILABLE
    except ImportError:
        PHONENUMBERS_AVAILABLE = False
    if not PHONENUMBERS_AVAILABLE:
        log.warning(
            "phonenumbers_unavailable",
            note="Falling back to dual PHONE_US/PHONE_E164 regex.",
        )

    pii_cfg = getattr(config, "pii", None)
    detectors_cfg = getattr(pii_cfg, "detectors", None)
    spacy_ner_enabled = bool(getattr(detectors_cfg, "spacy_ner", False))

    # Build the picklable detector_toggles dict consumed by workers.  These
    # surface PiiDetectorsConfig knobs that were previously declared but never
    # consulted.  detect_secrets has no consumer yet — kept as a forward-compat
    # placeholder; see the # TODO inside _validate.
    detector_toggles: dict[str, Any] = {
        "hyperscan": bool(getattr(detectors_cfg, "hyperscan", True)),
        "luhn_validation": bool(getattr(detectors_cfg, "luhn_validation", True)),
        "stdnum_validators": list(
            getattr(detectors_cfg, "stdnum_validators", []) or []
        ),
        # TODO: not yet wired — config field is currently advisory.
        "detect_secrets": bool(getattr(detectors_cfg, "detect_secrets", True)),
    }

    # Persistence threshold (F4.5).  A finding is written only when
    # match_rate >= threshold OR validated=True OR name_prior=True.
    match_rate_threshold = float(getattr(pii_cfg, "match_rate_threshold", 0.05))

    if spacy_ner_enabled:
        try:
            from discovery.pii_ner import availability_message

            log.info("pii_ner_status", message=availability_message())
        except ImportError:
            log.info("pii_ner_status", message="pii_ner module unavailable")

    TEXT_CLASSES = ("STRING_SHORT", "STRING_LONG")
    with engine.connect() as conn:
        # LEFT JOIN run_log to filter out columns whose latest pii_scan run
        # status is 'succeeded'.  Failed / started / unscanned all proceed.
        rl = run_log_t.alias("rl")
        rows = conn.execute(
            select(
                col_inventory_t.c.column_id,
                col_inventory_t.c.column_name,
                col_inventory_t.c.table_id,
                col_inventory_t.c.type_class,
                tbl_inventory_t.c.parquet_path,
                rl.c.status.label("rl_status"),
            )
            .select_from(
                col_inventory_t
                .join(
                    tbl_inventory_t,
                    tbl_inventory_t.c.table_id == col_inventory_t.c.table_id,
                )
                .outerjoin(
                    rl,
                    and_(
                        rl.c.phase == "pii_scan",
                        rl.c.scope_type == "column",
                        rl.c.scope_id == col_inventory_t.c.column_id,
                    ),
                )
            )
            .where(
                and_(
                    col_inventory_t.c.type_class.in_(TEXT_CLASSES),
                    tbl_inventory_t.c.parquet_path.is_not(None),
                    tbl_inventory_t.c.status == "extracted",
                )
            )
        ).mappings().all()

    pending = [
        r for r in rows
        if r["parquet_path"] and r["rl_status"] != "succeeded"
    ]
    skipped_done = sum(1 for r in rows if r["rl_status"] == "succeeded")
    log.info(
        "pii_scan_eligible",
        total=len(rows),
        pending=len(pending),
        already_succeeded=skipped_done,
    )
    if not pending:
        log.info("pii_scan_nothing_to_do")
        return

    settings: dict = {
        "max_rows": getattr(pii_cfg, "scan_rows_per_column", 50_000),
        "max_examples": 3,
        "enable_ner": spacy_ner_enabled,
        "detector_toggles": detector_toggles,
    }

    tasks = [
        (row["column_id"], row["parquet_path"], row["column_name"], row["type_class"])
        for row in pending
    ]

    orch_cfg = getattr(config, "orchestration", None)
    workers_cfg = getattr(orch_cfg, "workers", None)
    num_workers: int = getattr(workers_cfg, "pii_scan", 16)

    log.info(
        "pii_scan_pool_start",
        workers=num_workers,
        tasks=len(tasks),
    )

    with multiprocessing.Pool(
        processes=num_workers,
        initializer=_worker_init,
        initargs=(settings,),
    ) as pool:
        all_results = pool.map(_pii_task, tasks)

    now = datetime.now(timezone.utc)
    success = failed = total_findings = 0

    pending_writes: list[tuple[int, list[dict[str, Any]]]] = []

    def _flush() -> None:
        nonlocal success, total_findings
        if not pending_writes:
            return
        with txn(engine) as conn:
            dao = PiiFinding(conn)
            for _column_id, finding_list in pending_writes:
                for fd in finding_list:
                    dao.upsert({**fd, "detected_at": now})
                    total_findings += 1
        for column_id, _ in pending_writes:
            run_log.succeed("pii_scan", "column", column_id)
        success += len(pending_writes)
        pending_writes.clear()

    threshold_dropped = 0

    for task_args, finding_list in zip(tasks, all_results):
        column_id = task_args[0]
        if finding_list is None:
            failed += 1
            run_log.fail("pii_scan", "column", column_id, "pii_task returned None")
            continue
        # F4.5 persistence gate — keep a finding only when ANY of:
        #   * match_rate >= operator-configured threshold (validated rate)
        #   * the validator accepted at least one row (validated=True)
        #   * the column name strongly implies the type (name_prior=True)
        # The OR ensures name-anchored / validated findings are never dropped
        # by a noisy threshold.  Findings dropped by the gate never reach the
        # DB and are tracked in ``threshold_dropped`` for the summary log.
        kept: list[dict[str, Any]] = []
        for fd in finding_list:
            if (
                float(fd.get("match_rate", 0.0)) >= match_rate_threshold
                or bool(fd.get("validated"))
                or bool(fd.get("name_prior"))
            ):
                kept.append(fd)
            else:
                threshold_dropped += 1
        pending_writes.append((column_id, kept))
        if len(pending_writes) >= _PERSIST_BATCH:
            try:
                _flush()
            except Exception as exc:
                log.error(
                    "pii_scan_batch_flush_failed",
                    exc=str(exc),
                    exc_info=True,
                )
                for column_id, _ in pending_writes:
                    run_log.fail("pii_scan", "column", column_id, str(exc))
                failed += len(pending_writes)
                pending_writes.clear()

    try:
        _flush()
    except Exception as exc:
        log.error("pii_scan_final_flush_failed", exc=str(exc), exc_info=True)
        for column_id, _ in pending_writes:
            run_log.fail("pii_scan", "column", column_id, str(exc))
        failed += len(pending_writes)
        pending_writes.clear()

    log.info(
        "pii_scan_complete",
        success=success,
        failed=failed,
        total_findings=total_findings,
        threshold_dropped=threshold_dropped,
        match_rate_threshold=match_rate_threshold,
    )
