"""
pii_patterns.py — Catalog of PII regex patterns.

This module exposes:
  * ``PatternDef`` — a single PII pattern's metadata
  * ``PATTERNS`` — the canonical list (existing 8 + 39 new from the upgrade plan)
  * ``PATTERN_DEFS`` — alias of ``PATTERNS`` retained for backward compatibility
                      with ``pii_scan`` callers that imported the old name.

Each pattern carries:
  - ``name``        : symbolic id (e.g. ``"PASSPORT_US"``)
  - ``regex_bytes`` : pattern as bytes (Hyperscan-friendly form)
  - ``validator``   : symbolic key into a validator dispatch table; ``None`` if
                      the regex is the only filter
  - ``locale``      : ISO-3166-1 alpha-2 country code, ``"global"``, or ``None``
  - ``regulated``   : tuple of regulation labels (``"GDPR"``, ``"HIPAA"``, ...)
  - ``specificity`` : higher = more specific; used to break ties in
                      span-overlap resolution (IBAN=10 ≫ PHONE_US=4)
  - ``fp_class``    : ``"R"`` regex-only, ``"C"`` needs checksum,
                      ``"H"`` high false-positive without anchor

The pattern set is intentionally append-only: removing or renaming a pattern
risks invalidating cached findings keyed by ``pii_type``.  Add new patterns at
the bottom of the list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class PatternDef:
    """Metadata for a single PII regex pattern.

    The dataclass is ``frozen=True`` so a single ``PATTERNS`` list can be safely
    shared between worker processes (Hyperscan workers each rebuild their own
    Database, but the underlying pattern definitions never mutate).
    """

    name: str
    regex_bytes: bytes
    validator: Optional[str] = None
    locale: Optional[str] = None
    regulated: tuple[str, ...] = field(default_factory=tuple)
    specificity: int = 50
    fp_class: str = "R"


# ---------------------------------------------------------------------------
# Existing 8 patterns (preserved verbatim from earlier pii_scan.py)
# ---------------------------------------------------------------------------

_EXISTING_PATTERNS: list[PatternDef] = [
    PatternDef(
        name="EMAIL",
        regex_bytes=rb"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
        validator=None,
        locale="global",
        regulated=("GDPR", "CCPA"),
        specificity=7,
        fp_class="R",
    ),
    PatternDef(
        name="PHONE_US",
        regex_bytes=rb"(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}",
        # ``phone`` validator delegates to ``pii_locale.phone_valid`` —
        # ``phonenumbers``-backed when installed, permissive otherwise.
        # This wires D4 (locale-aware PHONE) without breaking the
        # dual-regex fallback contract.
        validator="phone",
        locale="US",
        regulated=("CCPA",),
        specificity=4,
        fp_class="H",
    ),
    PatternDef(
        name="PHONE_E164",
        regex_bytes=rb"\+\d{7,15}",
        validator="phone",
        locale="global",
        regulated=("GDPR",),
        specificity=4,
        fp_class="H",
    ),
    PatternDef(
        name="SSN_US",
        regex_bytes=rb"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
        validator="ssn_us",
        locale="US",
        regulated=("CCPA", "HIPAA"),
        specificity=8,
        fp_class="R",
    ),
    PatternDef(
        name="CC_NUMBER",
        regex_bytes=rb"\b(?:\d[ \-]?){13,19}\b",
        validator="luhn",
        locale="global",
        regulated=("PCI",),
        specificity=9,
        fp_class="C",
    ),
    PatternDef(
        name="IBAN",
        regex_bytes=rb"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b",
        validator="iban",
        locale="global",
        regulated=("PCI", "GDPR"),
        specificity=10,
        fp_class="C",
    ),
    PatternDef(
        name="API_KEY",
        regex_bytes=rb"\b(?:[A-Za-z0-9_\-]{32,}|[A-Fa-f0-9]{32,})\b",
        validator="entropy",
        locale="global",
        regulated=("SOX",),
        specificity=3,
        fp_class="H",
    ),
    PatternDef(
        name="DOB",
        regex_bytes=rb"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b",
        validator="dob",
        locale="global",
        regulated=("GDPR", "HIPAA"),
        specificity=5,
        fp_class="H",
    ),
]

# ---------------------------------------------------------------------------
# 39 new patterns from the §A catalog
# ---------------------------------------------------------------------------
#
# Notes on regex choices:
#   * Hyperscan supports PCRE-lite (no backreferences, no lookbehind beyond
#     fixed-width).  The regexes below stick to alternation + character
#     classes + bounded quantifiers + word boundaries.
#   * Locale-specific patterns (e.g. UK_PASSPORT) are deliberately short — the
#     validator (or column-name prior) is what gates them as PII; the regex
#     surfaces *candidates*.
#   * Specificity ranking (used for overlap tie-breaks):
#         IBAN=10, CC=9, SSN/PASSPORT/AADHAAR/CPF/CURP/PESEL=8,
#         national-id=7, EMAIL=7, JWT/PEM=7, AWS_KEY=8, GH_PAT=8,
#         PHONE=4, IP/MAC=4, GEO_COORD=3, POSTAL_CODE=3, ICD10=3, MRN=2.

_NEW_PATTERNS: list[PatternDef] = [
    # 1. US passport (one letter + 8 digits or two letters + 7 digits)
    PatternDef(
        name="PASSPORT_US",
        regex_bytes=rb"\b(?:[A-Z]\d{8}|[A-Z]{2}\d{7})\b",
        validator=None,
        locale="US",
        regulated=("CCPA",),
        specificity=8,
        fp_class="H",
    ),
    # 2. UK passport: 9 digits — heavy FP, gate by name prior
    PatternDef(
        name="PASSPORT_GB",
        regex_bytes=rb"\b\d{9}\b",
        validator=None,
        locale="GB",
        regulated=("GDPR",),
        specificity=8,
        fp_class="H",
    ),
    # 3. India passport: starts A-PR-WY, then 1-9, then 6 digits
    PatternDef(
        name="PASSPORT_IN",
        regex_bytes=rb"\b[A-PR-WY][1-9]\d{6}\b",
        validator=None,
        locale="IN",
        regulated=("DPDPA",),
        specificity=7,
        fp_class="R",
    ),
    # 4. US driver's license — coarse pattern (state-specific in practice)
    PatternDef(
        name="DL_US",
        regex_bytes=rb"\b[A-Z]\d{7,12}\b",
        validator=None,
        locale="US",
        regulated=("CCPA", "HIPAA"),
        specificity=6,
        fp_class="H",
    ),
    # 5. UK NHS number: 10 digits
    PatternDef(
        name="NHS_GB",
        regex_bytes=rb"\b\d{3}[ \-]?\d{3}[ \-]?\d{4}\b",
        validator="nhs_gb",
        locale="GB",
        regulated=("GDPR",),
        specificity=7,
        fp_class="C",
    ),
    # 6. US ITIN: 9XX-7X-XXXX or 9XX-8X-XXXX
    PatternDef(
        name="ITIN_US",
        regex_bytes=rb"\b9\d{2}-[78]\d-\d{4}\b",
        validator=None,
        locale="US",
        regulated=("CCPA",),
        specificity=8,
        fp_class="R",
    ),
    # 7. US Medicare beneficiary identifier
    PatternDef(
        name="MEDICARE_MBI_US",
        regex_bytes=rb"\b[1-9][AC-HJKMNP-RT-Y][A-Z0-9]\d[AC-HJKMNP-RT-Y][A-Z0-9]\d[AC-HJKMNP-RT-Y]{2}\d{2}\b",
        validator=None,
        locale="US",
        regulated=("HIPAA",),
        specificity=8,
        fp_class="R",
    ),
    # 8. US National Provider Identifier (10 digits, Luhn-mod10 with 80840)
    PatternDef(
        name="NPI_US",
        regex_bytes=rb"\b\d{10}\b",
        validator="npi_us",
        locale="US",
        regulated=("HIPAA",),
        specificity=7,
        fp_class="C",
    ),
    # 9. UK NINO — letters + 6 digits + suffix
    PatternDef(
        name="NINO_GB",
        regex_bytes=rb"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b",
        validator="nino_gb",
        locale="GB",
        regulated=("GDPR",),
        specificity=8,
        fp_class="C",
    ),
    # 10. India Aadhaar — 12 digits, optional spaces every 4
    PatternDef(
        name="AADHAAR_IN",
        regex_bytes=rb"\b\d{4}\s?\d{4}\s?\d{4}\b",
        validator="aadhaar_in",
        locale="IN",
        regulated=("DPDPA",),
        specificity=8,
        fp_class="C",
    ),
    # 11. India PAN — 5 letters + 4 digits + 1 letter
    PatternDef(
        name="PAN_IN",
        regex_bytes=rb"\b[A-Z]{5}\d{4}[A-Z]\b",
        validator="pan_in",
        locale="IN",
        regulated=("DPDPA",),
        specificity=8,
        fp_class="C",
    ),
    # 12. Singapore NRIC
    PatternDef(
        name="NRIC_SG",
        regex_bytes=rb"\b[STFG]\d{7}[A-Z]\b",
        validator="nric_sg",
        locale="SG",
        regulated=("PDPA",),
        specificity=8,
        fp_class="C",
    ),
    # 13. Brazil CPF
    PatternDef(
        name="CPF_BR",
        regex_bytes=rb"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
        validator="cpf_br",
        locale="BR",
        regulated=("LGPD",),
        specificity=8,
        fp_class="C",
    ),
    # 14. Mexico CURP — 18 alnum
    PatternDef(
        name="CURP_MX",
        regex_bytes=rb"\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b",
        validator="curp_mx",
        locale="MX",
        regulated=("LFPDPPP",),
        specificity=8,
        fp_class="C",
    ),
    # 15. Spain DNI / NIE: 8 digits + letter (or X/Y/Z + 7 + letter)
    PatternDef(
        name="DNI_ES",
        regex_bytes=rb"\b(?:\d{8}|[XYZ]\d{7})[A-Z]\b",
        validator="dni_es",
        locale="ES",
        regulated=("GDPR",),
        specificity=7,
        fp_class="C",
    ),
    # 16. France NIR (social security): 13 + 2 key digits
    PatternDef(
        name="NIR_FR",
        regex_bytes=rb"\b[12]\d{2}(?:0[1-9]|1[0-2])(?:\d{2})\d{3}\d{3}(?:\d{2})\b",
        validator="nir_fr",
        locale="FR",
        regulated=("GDPR",),
        specificity=7,
        fp_class="C",
    ),
    # 17. Germany tax ID (11 digits, ISO 7064)
    PatternDef(
        name="TAX_ID_DE",
        regex_bytes=rb"\b\d{11}\b",
        validator="idnr_de",
        locale="DE",
        regulated=("GDPR",),
        specificity=7,
        fp_class="C",
    ),
    # 18. Italy Codice Fiscale — 16 alnum
    PatternDef(
        name="CODICE_FISCALE_IT",
        regex_bytes=rb"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b",
        validator="codice_it",
        locale="IT",
        regulated=("GDPR",),
        specificity=8,
        fp_class="C",
    ),
    # 19. Poland PESEL — 11 digits, mod-10
    PatternDef(
        name="PESEL_PL",
        regex_bytes=rb"\b\d{11}\b",
        validator="pesel_pl",
        locale="PL",
        regulated=("GDPR",),
        specificity=7,
        fp_class="C",
    ),
    # 20. Netherlands BSN — 9 digits, 11-test
    PatternDef(
        name="BSN_NL",
        regex_bytes=rb"\b\d{9}\b",
        validator="bsn_nl",
        locale="NL",
        regulated=("GDPR",),
        specificity=7,
        fp_class="C",
    ),
    # 21. Sweden Personnummer — YYMMDD-NNNN or 10/12 digit form
    PatternDef(
        name="PERSONNUMMER_SE",
        regex_bytes=rb"\b\d{6}[-+]?\d{4}\b",
        validator="personnummer_se",
        locale="SE",
        regulated=("GDPR",),
        specificity=8,
        fp_class="C",
    ),
    # 22. EU VAT — 2-letter prefix + 2-12 alnum
    PatternDef(
        name="VAT_EU",
        regex_bytes=rb"\b(AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|GB|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK)[A-Z0-9]{2,12}\b",
        validator="vat_eu",
        locale="EU",
        regulated=("GDPR",),
        specificity=6,
        fp_class="C",
    ),
    # 23. SWIFT BIC — 8 or 11 chars
    PatternDef(
        name="SWIFT_BIC",
        regex_bytes=rb"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b",
        validator="bic",
        locale="global",
        regulated=("PCI",),
        specificity=7,
        fp_class="C",
    ),
    # 24. ABA routing number — 9 digits, mod-10 weighted (validator=None as
    #     stdnum.us.aba isn't shipped in this env; use name-prior to gate FP).
    PatternDef(
        name="ABA_ROUTING_US",
        regex_bytes=rb"\b\d{9}\b",
        validator="aba_us",
        locale="US",
        regulated=("GLBA",),
        specificity=6,
        fp_class="C",
    ),
    # 25. IPv4 address
    PatternDef(
        name="IPV4",
        regex_bytes=rb"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
        validator="ipv4",
        locale="global",
        regulated=("GDPR",),
        specificity=6,
        fp_class="R",
    ),
    # 26. IPv6 address — RFC 4291 (simplified, hyperscan-compatible)
    PatternDef(
        name="IPV6",
        regex_bytes=(
            rb"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
            rb"|(?:[0-9A-Fa-f]{1,4}:){1,7}:"
            rb"|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}"
        ),
        validator=None,
        locale="global",
        regulated=("GDPR",),
        specificity=6,
        fp_class="R",
    ),
    # 27. MAC address
    PatternDef(
        name="MAC_ADDR",
        regex_bytes=rb"\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b",
        validator=None,
        locale="global",
        regulated=("GDPR",),
        specificity=6,
        fp_class="R",
    ),
    # 28. AWS access key id — fixed prefix
    PatternDef(
        name="AWS_ACCESS_KEY_ID",
        regex_bytes=rb"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)[0-9A-Z]{16}\b",
        validator=None,
        locale="global",
        regulated=("SOX",),
        specificity=8,
        fp_class="R",
    ),
    # 29. AWS secret access key — high-FP without proximity to access-key-id
    PatternDef(
        name="AWS_SECRET",
        regex_bytes=rb"\b[A-Za-z0-9/+=]{40}\b",
        validator="entropy",
        locale="global",
        regulated=("SOX",),
        specificity=4,
        fp_class="H",
    ),
    # 30. GCP API key — fixed prefix AIza + 35 base64-url chars
    PatternDef(
        name="GCP_API_KEY",
        regex_bytes=rb"\bAIza[0-9A-Za-z\-_]{35}\b",
        validator=None,
        locale="global",
        regulated=("SOX",),
        specificity=8,
        fp_class="R",
    ),
    # 31. JSON Web Token — three base64-url segments
    PatternDef(
        name="JWT",
        regex_bytes=rb"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
        validator="jwt",
        locale="global",
        regulated=("SOX",),
        specificity=7,
        fp_class="C",
    ),
    # 32. GitHub personal access token
    PatternDef(
        name="GH_PAT",
        regex_bytes=rb"\bghp_[A-Za-z0-9]{36}\b",
        validator=None,
        locale="global",
        regulated=("SOX",),
        specificity=8,
        fp_class="R",
    ),
    # 33. PEM-encoded private key header
    PatternDef(
        name="PRIVATE_KEY_PEM",
        regex_bytes=rb"-----BEGIN [A-Z ]+PRIVATE KEY-----",
        validator=None,
        locale="global",
        regulated=("SOX",),
        specificity=9,
        fp_class="R",
    ),
    # 34. Bitcoin address (legacy P2PKH/P2SH, base58)
    PatternDef(
        name="BTC_ADDR",
        regex_bytes=rb"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b",
        validator=None,
        locale="global",
        regulated=("AML",),
        specificity=7,
        fp_class="C",
    ),
    # 35. Ethereum address — 0x + 40 hex chars
    PatternDef(
        name="ETH_ADDR",
        regex_bytes=rb"\b0x[a-fA-F0-9]{40}\b",
        validator=None,
        locale="global",
        regulated=("AML",),
        specificity=8,
        fp_class="R",
    ),
    # 36. Medical record number — generic 6-10 digits, gated by name prior
    PatternDef(
        name="MRN",
        regex_bytes=rb"\b\d{6,10}\b",
        validator=None,
        locale="global",
        regulated=("HIPAA",),
        specificity=2,
        fp_class="H",
    ),
    # 37. ICD-10 diagnostic code
    PatternDef(
        name="ICD10",
        regex_bytes=rb"\b[A-TV-Z]\d{2}(?:\.\d{1,4})?\b",
        validator=None,
        locale="global",
        regulated=("HIPAA",),
        specificity=3,
        fp_class="H",
    ),
    # 38. Geographic coordinate pair
    PatternDef(
        name="GEO_COORD",
        regex_bytes=rb"[-+]?\d{1,2}\.\d+,\s*[-+]?\d{1,3}\.\d+",
        validator="geo_coord",
        locale="global",
        regulated=("GDPR",),
        specificity=3,
        fp_class="R",
    ),
    # 39. Postal code (US ZIP / UK / DE / IN / generic) — locale-validated
    PatternDef(
        name="POSTAL_CODE",
        regex_bytes=(
            rb"\b(?:\d{5}(?:-\d{4})?"           # US ZIP+4
            rb"|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}"   # UK
            rb"|\d{6}"                         # IN / DE / generic
            rb")\b"
        ),
        validator=None,
        locale="global",
        regulated=("GDPR",),
        specificity=3,
        fp_class="H",
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

PATTERNS: list[PatternDef] = list(_EXISTING_PATTERNS) + list(_NEW_PATTERNS)

# Backward-compatible alias — the old ``pii_scan.PATTERN_DEFS`` callers
# keep working without source changes.
PATTERN_DEFS: list[PatternDef] = PATTERNS


def get_pattern(name: str) -> Optional[PatternDef]:
    """Return the PatternDef with the given symbolic ``name``, or ``None``."""
    for p in PATTERNS:
        if p.name == name:
            return p
    return None


# Specificity rank table — used by the overlap resolver.  A name absent here
# falls back to its PatternDef.specificity.
SPECIFICITY: dict[str, int] = {p.name: p.specificity for p in PATTERNS}
