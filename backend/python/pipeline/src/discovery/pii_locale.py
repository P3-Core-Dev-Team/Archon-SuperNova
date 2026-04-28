"""
pii_locale.py — Locale-aware validators.

This module owns:
  * ``phone_valid()`` — uses ``phonenumbers`` if installed, else returns
    ``True`` (fall back to regex-only filtering elsewhere).
  * Lazy-loaded ``stdnum`` validators for country-specific national IDs.

All validator dispatch goes through :data:`LOCALE_VALIDATORS`, a mapping from
the symbolic ``validator`` name on a :class:`PatternDef` to a callable
``(value: str) -> bool``.

Lazy imports
------------
``stdnum`` ships ~200 sub-modules, but the test environment may be missing a
few (e.g. ``stdnum.us.aba``).  We attempt the import inside a ``try`` and if
it fails we register a permissive validator that returns ``True`` — this
avoids dropping every match for that pattern just because the validator is
absent.  The Bayesian ``π_validate`` will simply stay at the regex match rate.

If ``phonenumbers`` is not installed, :data:`PHONENUMBERS_AVAILABLE` is set
to ``False`` and ``phone_valid`` returns ``True``; the dual-regex fallback in
``pii_patterns`` (``PHONE_US`` + ``PHONE_E164``) covers the gap.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# phonenumbers — optional
# ---------------------------------------------------------------------------

try:
    import phonenumbers as _phonenumbers  # type: ignore[import]

    PHONENUMBERS_AVAILABLE = True
except ImportError:
    _phonenumbers = None  # type: ignore[assignment]
    PHONENUMBERS_AVAILABLE = False


def phone_valid(value: str, default_region: str = "US") -> bool:
    """Return True if *value* parses+validates as a phone number.

    If the ``phonenumbers`` library is unavailable, return ``True`` so the
    regex-based ``PHONE_US`` / ``PHONE_E164`` filters remain authoritative.
    """
    if not PHONENUMBERS_AVAILABLE or _phonenumbers is None:
        return True
    try:
        parsed = _phonenumbers.parse(value, default_region)
        return bool(_phonenumbers.is_valid_number(parsed))
    except Exception:
        # phonenumbers raises NumberParseException for unparseable inputs;
        # any other exception we treat as a failed validation.
        return False


# ---------------------------------------------------------------------------
# stdnum lazy loader
# ---------------------------------------------------------------------------


def _load_stdnum_validator(module_path: str) -> Optional[Callable[[str], bool]]:
    """Attempt to import ``stdnum.<module_path>`` and return ``.is_valid``.

    Returns ``None`` if the module cannot be imported — callers should treat
    a ``None`` validator as "permissive" (regex hit is sufficient).
    """
    try:
        import importlib

        mod = importlib.import_module(f"stdnum.{module_path}")
        return getattr(mod, "is_valid", None)  # type: ignore[no-any-return]
    except (ImportError, AttributeError) as exc:
        log.debug("stdnum.%s unavailable: %s", module_path, exc)
        return None


# Resolve all stdnum validators once at import-time.  Missing modules log at
# DEBUG level and become permissive validators.
_IBAN = _load_stdnum_validator("iban")
_AADHAAR_IN = _load_stdnum_validator("in_.aadhaar")
_PAN_IN = _load_stdnum_validator("in_.pan")
_CPF_BR = _load_stdnum_validator("br.cpf")
_CURP_MX = _load_stdnum_validator("mx.curp")
_DNI_ES = _load_stdnum_validator("es.dni")
_NIE_ES = _load_stdnum_validator("es.nie")
_NIR_FR = _load_stdnum_validator("fr.nir")
_IDNR_DE = _load_stdnum_validator("de.idnr")
_CODICE_IT = _load_stdnum_validator("it.codicefiscale")
_PESEL_PL = _load_stdnum_validator("pl.pesel")
_BSN_NL = _load_stdnum_validator("nl.bsn")
_PERSONNUMMER_SE = _load_stdnum_validator("se.personnummer")
_NRIC_SG = _load_stdnum_validator("sg.nric")
_BIC = _load_stdnum_validator("bic")
_NHS_GB = _load_stdnum_validator("gb.nhs")
_NINO_GB = _load_stdnum_validator("gb.nino")
_VAT_EU = _load_stdnum_validator("eu.vat")
_ITIN_US = _load_stdnum_validator("us.itin")
_NPI_US = _load_stdnum_validator("us.npi")
_ABA_US = _load_stdnum_validator("us.aba")


# Validators whose underlying regex collapses to "any N-digit string" — when
# the matching stdnum module is unavailable, returning permissively True would
# carpet-flag every numeric column of the right length.  These names
# default-DENY (return False) instead of default-allow when the stdnum module
# is missing.  See F4.4 in the post-impl review and pii_scan._HIGH_FP_PII_TYPES.
LOCALE_VALIDATORS_FALLBACK_DENY: set[str] = {
    "bsn_nl",       # PASSPORT_GB and BSN_NL share the \b\d{9}\b regex
    "pesel_pl",     # 11 digits
    "idnr_de",      # 11 digits (TAX_ID_DE)
    "npi_us",       # 10 digits
    # Note: PASSPORT_GB has validator=None on its PatternDef and is gated
    # at the pii_scan._validate level via _HIGH_FP_PII_TYPES rather than here.
}


def _wrap_stdnum(
    fn: Optional[Callable[[str], bool]],
    *,
    name: Optional[str] = None,
) -> Callable[[str], bool]:
    """Wrap a stdnum validator with the permissive-or-deny contract.

    Behaviour
    ---------
    * ``fn is not None`` — call it; on any exception return ``False``.
    * ``fn is None`` and ``name`` in :data:`LOCALE_VALIDATORS_FALLBACK_DENY` —
      hard-fail to ``False`` (high-FP regex without checksum is unsafe).
    * ``fn is None`` otherwise — permissive ``True`` (legacy contract; the
      regex is the only filter and the Bayesian ``π_validate`` stays at the
      raw match rate).
    """
    if fn is None:
        if name is not None and name in LOCALE_VALIDATORS_FALLBACK_DENY:
            return lambda v: False
        return lambda v: True

    def _v(value: str) -> bool:
        try:
            return bool(fn(value))
        except Exception:
            return False

    return _v


# ---------------------------------------------------------------------------
# IPv4 / GEO_COORD / VAT — light, library-free validators
# ---------------------------------------------------------------------------


def ipv4_in_range(value: str) -> bool:
    """Return True if *value* looks like an IPv4 with each octet in 0-255.

    The regex on PatternDef already enforces digits + dots; this is a defence
    in depth in case the regex matches something like ``999.0.0.0``.
    """
    parts = value.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def geo_coord_in_range(value: str) -> bool:
    """Return True if *value* is a ``lat,lon`` pair with sane ranges."""
    try:
        lat_str, lon_str = value.split(",", 1)
        lat = float(lat_str.strip())
        lon = float(lon_str.strip())
    except (ValueError, AttributeError):
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def vat_eu_valid(value: str) -> bool:
    """Validate EU VAT number via stdnum or fallback regex."""
    if _VAT_EU is not None:
        try:
            return bool(_VAT_EU(value))
        except Exception:
            return False
    # Fallback regex ensures we don't match arbitrary 2-letter + 2-12 alnum strings.
    return bool(re.match(r"^(AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|GB|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK)[A-Z0-9]{2,12}$", value))


def bic_valid(value: str) -> bool:
    """Validate SWIFT-BIC via stdnum or fallback regex."""
    if _BIC is not None:
        try:
            return bool(_BIC(value))
        except Exception:
            return False
    # Fallback regex enforces 4-bank + 2-country + 2-location + optional 3-branch.
    return bool(re.match(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$", value))


# ---------------------------------------------------------------------------
# Composite validator dispatch table
# ---------------------------------------------------------------------------
#
# Keyed by the symbolic ``validator`` field on PatternDef.  All entries are
# callables ``(str) -> bool``.

LOCALE_VALIDATORS: dict[str, Callable[[str], bool]] = {
    # Phone
    "phone": phone_valid,
    # Banking / financial
    "iban": _wrap_stdnum(_IBAN, name="iban"),
    "bic": bic_valid,
    "vat_eu": vat_eu_valid,
    "aba_us": _wrap_stdnum(_ABA_US, name="aba_us"),
    # Country IDs
    "aadhaar_in": _wrap_stdnum(_AADHAAR_IN, name="aadhaar_in"),
    "pan_in": _wrap_stdnum(_PAN_IN, name="pan_in"),
    "cpf_br": _wrap_stdnum(_CPF_BR, name="cpf_br"),
    "curp_mx": _wrap_stdnum(_CURP_MX, name="curp_mx"),
    "dni_es": _wrap_stdnum(_DNI_ES, name="dni_es"),
    "nie_es": _wrap_stdnum(_NIE_ES, name="nie_es"),
    "nir_fr": _wrap_stdnum(_NIR_FR, name="nir_fr"),
    "idnr_de": _wrap_stdnum(_IDNR_DE, name="idnr_de"),
    "codice_it": _wrap_stdnum(_CODICE_IT, name="codice_it"),
    "pesel_pl": _wrap_stdnum(_PESEL_PL, name="pesel_pl"),
    "bsn_nl": _wrap_stdnum(_BSN_NL, name="bsn_nl"),
    "personnummer_se": _wrap_stdnum(_PERSONNUMMER_SE, name="personnummer_se"),
    "nric_sg": _wrap_stdnum(_NRIC_SG, name="nric_sg"),
    "nhs_gb": _wrap_stdnum(_NHS_GB, name="nhs_gb"),
    "nino_gb": _wrap_stdnum(_NINO_GB, name="nino_gb"),
    "itin_us": _wrap_stdnum(_ITIN_US, name="itin_us"),
    "npi_us": _wrap_stdnum(_NPI_US, name="npi_us"),
    # Network / geographic
    "ipv4": ipv4_in_range,
    "geo_coord": geo_coord_in_range,
}


def get_validator(name: Optional[str]) -> Optional[Callable[[str], bool]]:
    """Return the locale-aware validator for *name*, or ``None``.

    The base ``pii_scan`` module also has its own dispatch table for the
    legacy validator names (``luhn``, ``entropy``, ``ssn_us``, ``dob``).
    Callers should check both tables.
    """
    if name is None:
        return None
    return LOCALE_VALIDATORS.get(name)
