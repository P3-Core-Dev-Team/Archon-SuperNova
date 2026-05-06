import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .pii_patterns import PATTERN_DEFS, SPECIFICITY, get_pattern
from .pii_priors import (
    is_credential_name,
    is_free_text_column_name,
    is_structural_pointer_name,
    name_prior,
    name_prior_strength,
)
from .pii_iin import card_brand as _card_brand
from .pii_score import Match as _ScoreMatch
from .pii_score import column_pii_confidence, resolve_overlaps


# === Validators (Luhn + entropy + locale-aware via stdnum / phonenumbers) ===

def _luhn_valid(number_str: str) -> bool:
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


def _entropy_looks_random(value: str, threshold: float = 3.5) -> bool:
    if len(value) < 16:
        return False
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    entropy = -sum((c / n) * math.log2(c / n) for c in counts.values() if c > 0)
    return entropy >= threshold


def _ssn_us_valid(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 9:
        return False
    if digits[:3] in {"000", "666"} or digits[:3].startswith("9"):
        return False
    if digits[3:5] == "00" or digits[5:] == "0000":
        return False
    return True


def _date_parseable(value: str) -> bool:
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            datetime.strptime(value.strip(), fmt)
            return True
        except ValueError:
            pass
    return False


# Locale-aware stdnum / phonenumbers — pulled from pii_locale on demand to
# keep import surface thin.  Anything that fails to import returns False.
def _validate(pii_type: str, matched: str, validator_name: Optional[str]) -> bool:
    if validator_name is None:
        return True
    if validator_name == "luhn":
        return _luhn_valid(matched)
    if validator_name == "entropy":
        return _entropy_looks_random(matched)
    if validator_name == "ssn_us":
        return _ssn_us_valid(matched)
    if validator_name == "date":
        return _date_parseable(matched)
    try:
        from . import pii_locale
        fn = getattr(pii_locale, validator_name, None)
        if callable(fn):
            try:
                return bool(fn(matched))
            except Exception:
                return False
    except ImportError:
        pass
    return False


def _redact(value: str, pii_type: str) -> str:
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


# === Pre-compiled regex matcher built from PATTERN_DEFS =====================

@dataclass(frozen=True)
class _Compiled:
    name: str
    regex: re.Pattern
    validator: Optional[str]


def _compile_patterns() -> list[_Compiled]:
    out: list[_Compiled] = []
    for p in PATTERN_DEFS:
        try:
            # PatternDef stores the regex as ``regex_bytes`` (Hyperscan's
            # native shape).  Decode + compile for the Python re fallback.
            pat = p.regex_bytes.decode("utf-8") if isinstance(p.regex_bytes, (bytes, bytearray)) else str(p.regex_bytes)
            out.append(_Compiled(name=p.name, regex=re.compile(pat), validator=p.validator))
        except re.error:
            continue
    return out


_COMPILED: Optional[list[_Compiled]] = None


def _compiled() -> list[_Compiled]:
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = _compile_patterns()
    return _COMPILED


# === The scanner =============================================================


class PiiScanner:
    """
    Stage 11: Bayesian PII detection over column samples.

    Algorithm (mirrors the SuperNova pipeline's scan_column):
      * Run every regex in the catalogue against each cell.
      * Validate matches via Luhn / entropy / stdnum / phonenumbers / etc.
      * Resolve overlapping matches by specificity (CC_NUMBER > PHONE_US
        when both fit the same span).
      * Apply column-name priors so obviously-named columns surface
        even at low regex match rate.
      * Suppress false positives on structural-pointer / credential-storage
        / high-FP columns without a positive prior.
      * Emit Bayesian-scored findings (score = 1 − (1−π_name)(1−π_match)(1−π_validate)).

    Caller responsibility: provide ``values`` (a list of cell strings).  The
    parquet read / DuckDB sample lives outside this class — keeps the surface
    callable from any pipeline that already has data in memory.
    """

    _NAME_PRIOR_REQUIRED = frozenset({"CARD_HOLDER_NAME", "CARD_CVV", "AADHAAR_IN"})

    @staticmethod
    def scan_values(
        column_name: str,
        values: list[str],
        type_class: str = "STRING_LONG",
        enable_ner: bool = False,
        max_examples: int = 3,
    ) -> list[dict]:
        if not values:
            return []

        regex_match_counts: dict[str, int] = {}
        validated_counts: dict[str, int] = {}
        examples: dict[str, list[str]] = {}
        cc_brand_counts: dict[str, int] = {}

        ner_counts: dict[str, int] = {}
        ner_examples: dict[str, list[str]] = {}
        _ner_scan_text: Optional[Callable[[str], list]] = None
        ner_active = enable_ner and type_class == "STRING_LONG"
        if ner_active:
            try:
                from .pii_ner import scan_text as _ner_scan_text  # type: ignore[no-redef]
            except ImportError:
                _ner_scan_text = None

        rows_scanned = 0
        compiled = _compiled()

        for val in values:
            if not isinstance(val, str) or not val:
                continue
            rows_scanned += 1

            score_matches: list[_ScoreMatch] = []
            for cp in compiled:
                for m in cp.regex.finditer(val):
                    matched = m.group(0)
                    is_valid = _validate(cp.name, matched, cp.validator)
                    score_matches.append(
                        _ScoreMatch(
                            name=cp.name,
                            value=matched,
                            start=m.start(),
                            end=m.end(),
                            validated=is_valid,
                        )
                    )

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
                        bucket.append(_redact(m.value, m.name))
                    if m.name == "CC_NUMBER":
                        brand = _card_brand(m.value)
                        if brand:
                            cc_brand_counts[brand] = cc_brand_counts.get(brand, 0) + 1

            if _ner_scan_text is not None:
                try:
                    for ent in _ner_scan_text(val):
                        ner_counts[ent.pii_type] = ner_counts.get(ent.pii_type, 0) + 1
                        bucket = ner_examples.setdefault(ent.pii_type, [])
                        if len(bucket) < max_examples:
                            bucket.append(_redact(ent.text, ent.pii_type))
                except Exception:
                    pass

        if rows_scanned == 0:
            return []

        # === Materialise findings ============================================
        findings: list[dict] = []

        prior_type = name_prior(column_name)
        prior_strength = name_prior_strength(column_name, prior_type) if prior_type else 0.0
        is_struct_pointer = is_structural_pointer_name(column_name)
        is_cred = is_credential_name(column_name)
        is_free_text = is_free_text_column_name(column_name)

        seen_types: set[str] = set()

        for pii_type, match_count in regex_match_counts.items():
            np_strength = name_prior_strength(column_name, pii_type)
            if is_struct_pointer and np_strength == 0.0:
                continue
            if is_cred and np_strength == 0.0:
                continue
            if pii_type in PiiScanner._NAME_PRIOR_REQUIRED and np_strength == 0.0:
                continue
            validated = validated_counts.get(pii_type, 0)
            rate_regex = match_count / max(rows_scanned, 1)
            rate_validated = validated / max(match_count, 1)
            score = column_pii_confidence(
                name_prior_strength=np_strength,
                regex_match_rate=rate_regex,
                validator_pass_rate=rate_validated,
                free_text_column=is_free_text,
            )
            spec = SPECIFICITY.get(pii_type, 50)
            provider_breakdown: list[dict] = []
            if pii_type == "CC_NUMBER" and cc_brand_counts:
                total = sum(cc_brand_counts.values()) or 1
                provider_breakdown = [
                    {"brand": b, "count": c, "share": round(c / total, 4)}
                    for b, c in sorted(
                        cc_brand_counts.items(),
                        key=lambda kv: (-kv[1], kv[0]),
                    )
                ]
            findings.append({
                "column": column_name,
                "pii_type": pii_type,
                "detector": "regex",
                "match_count": match_count,
                "validated_count": validated,
                "sample_count": rows_scanned,
                "redacted_examples": examples.get(pii_type, []),
                "name_prior": np_strength > 0.0,
                "regex_match_rate": round(rate_regex, 4),
                "score": round(score, 4),
                "specificity": spec,
                "provider_breakdown": provider_breakdown,
            })
            seen_types.add(pii_type)

        for ner_type, count in ner_counts.items():
            np_strength = name_prior_strength(column_name, ner_type)
            if is_struct_pointer and np_strength == 0.0:
                continue
            if is_cred and np_strength == 0.0:
                continue
            rate_regex = count / max(rows_scanned, 1)
            score = column_pii_confidence(
                name_prior_strength=np_strength,
                regex_match_rate=rate_regex,
                validator_pass_rate=1.0,
                free_text_column=is_free_text,
            )
            findings.append({
                "column": column_name,
                "pii_type": ner_type,
                "detector": "ner",
                "match_count": count,
                "validated_count": count,
                "sample_count": rows_scanned,
                "redacted_examples": ner_examples.get(ner_type, []),
                "name_prior": np_strength > 0.0,
                "regex_match_rate": round(rate_regex, 4),
                "score": round(score, 4),
                "specificity": 50,
                "provider_breakdown": [],
            })
            seen_types.add(ner_type)

        # Name-prior-only surface
        if prior_type is not None and prior_type not in seen_types and prior_strength > 0.0:
            rate_regex = regex_match_counts.get(prior_type, 0) / max(rows_scanned, 1)
            score = column_pii_confidence(
                name_prior_strength=prior_strength,
                regex_match_rate=rate_regex,
                validator_pass_rate=0.0,
            )
            findings.append({
                "column": column_name,
                "pii_type": prior_type,
                "detector": "name_prior",
                "match_count": 0,
                "validated_count": 0,
                "sample_count": rows_scanned,
                "redacted_examples": [],
                "name_prior": True,
                "regex_match_rate": round(rate_regex, 4),
                "score": round(score, 4),
                "specificity": SPECIFICITY.get(prior_type, 50),
                "provider_breakdown": [],
            })

        return findings

    @staticmethod
    def scan_columns(
        columns: list[dict],
        enable_ner: bool = False,
        max_examples: int = 3,
    ) -> list[dict]:
        """Bulk: each input row is ``{column_name, values, type_class?}``.
        Returns the flattened list of findings across every column."""
        out: list[dict] = []
        for c in columns:
            out.extend(
                PiiScanner.scan_values(
                    column_name=str(c.get("column_name", "")),
                    values=list(c.get("values", []) or []),
                    type_class=str(c.get("type_class", "STRING_LONG")),
                    enable_ner=enable_ner,
                    max_examples=max_examples,
                )
            )
        return out

    @staticmethod
    def card_brand(pan: str) -> Optional[str]:
        """Standalone IIN/BIN classifier — Visa / Mastercard / Amex /
        Discover / RuPay / etc.  Useful for tagging a single PAN
        without the full Bayesian pass."""
        return _card_brand(pan)
