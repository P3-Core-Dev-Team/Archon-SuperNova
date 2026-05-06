"""
pii_priors.py — Column-name → PII type prior table.

When a column is named ``ssn``, ``email_address``, or ``passport_no``, the name
itself is strong evidence that the column contains PII of a particular type.
This module exposes:

  * ``COLUMN_NAME_PRIORS`` — a mapping from a compiled, case-insensitive regex
    to a PII type label
  * ``name_prior(column_name) -> Optional[str]`` — return the most-specific
    PII type implied by the column name, else ``None``
  * ``name_prior_strength(column_name, pii_type) -> float`` — the Bayesian
    π_name component used by ``pii_score.column_pii_confidence``

The regex keys are intentionally word-boundary anchored so substrings like
``email_address`` do not light up an ``ssn`` prior.
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Prior table
# ---------------------------------------------------------------------------
#
# Keys: word-boundary, case-insensitive regex.  Each prior covers all common
# spellings (e.g. ``email`` / ``e_mail`` / ``email_address``).
# Values: the corresponding ``pii_type`` label that downstream code emits.
#
# Order matters for ambiguous strings — more specific keys are listed first
# (e.g. ``pan_no`` for India PAN before generic ``pan`` for Primary Account
# Number / credit card).

#
# Note on regex form: the matching layer (:func:`_normalise`) converts ``_``
# to spaces before applying these patterns, so a key like
# ``social security`` matches both ``social_security`` and
# ``social security`` cleanly.  Word-boundaries (``\b``) here are robust
# because the input has been normalised.

COLUMN_NAME_PRIORS: dict[re.Pattern[str], str] = {
    # --- Strong identifiers ----------------------------------------------
    re.compile(r"\b(ssn|social\s?security|tax\s?id\s?us)\b", re.I): "SSN_US",
    re.compile(r"\bitin\b", re.I): "ITIN_US",
    re.compile(r"\b(dob|date\s?of\s?birth|birth\s?date|birthday)\b", re.I): "DOB",
    re.compile(r"\b(passport|passport\s?no|passport\s?number)\b", re.I): "PASSPORT_US",
    re.compile(r"\b(dl\s?no|dl\s?number|drivers?\s?license|license\s?no)\b", re.I): "DL_US",
    re.compile(r"\b(mrn|medical\s?record|patient\s?id|chart\s?no)\b", re.I): "MRN",
    re.compile(r"\b(icd10?|diagnosis\s?code|dx\s?code)\b", re.I): "ICD10",

    # --- Banking / financial --------------------------------------------
    re.compile(r"\biban\b", re.I): "IBAN",
    re.compile(r"\b(bank\s?account|account\s?number|acct\s?no)\b", re.I): "BANK_ACCOUNT",
    re.compile(r"\b(routing\s?number|rtn|aba)\b", re.I): "ABA_ROUTING_US",
    re.compile(r"\b(swift|bic|bic\s?code)\b", re.I): "SWIFT_BIC",
    re.compile(
        r"\b(card\s?number|cc\s?no|credit\s?card|primary\s?account\s?number)\b",
        re.I,
    ): "CC_NUMBER",
    # Note: bare 'pan' is intentionally NOT a CC prior — it's ambiguous with
    # India PAN.  Either side-by-side context disambiguates, or callers can
    # supply additional names via configuration.

    # PCI cardholder name — matches before generic PERSON_NAME so a column
    # called ``card_holder_name`` doesn't get mis-tagged as a generic name.
    # Card holder names ARE person names (and inherit GDPR), but the PCI
    # tag is the operationally important one and the column-name evidence
    # is unambiguous.
    re.compile(
        r"\b(card\s?holder|card\s?holder\s?name|cardholder|name\s?on\s?card|cc\s?holder)\b",
        re.I,
    ): "CARD_HOLDER_NAME",
    # PCI card verification value — column-name driven.  3- or 4-digit
    # numerics are too generic to detect by content alone, so the prior is
    # the primary signal.  Coverage spans the spelling variants in real
    # schemas: cvv, cvc, csc, card_security_code, card_verification_value,
    # security_code, verification_value, cvv2.
    re.compile(
        r"\b(cvv|cvv2|cvc|csc|card\s?security\s?code|card\s?verification(?:\s?value)?|security\s?code|verification\s?value)\b",
        re.I,
    ): "CARD_CVV",

    # Credential storage — password / password_hash / pwd_hash / etc.
    # Listed before generic name patterns so a column called
    # ``password_hash`` resolves to CREDENTIAL_HASH rather than
    # something else.  See :func:`is_credential_name` for the
    # suppression that drops API_KEY / SOX tags on these columns.
    re.compile(
        r"\b(password|passwd|pwd|hashed\s?password|password\s?hash|password\s?digest|"
        r"passwd\s?hash|pwd\s?hash|user\s?pass|secret\s?hash|"
        r"credentials?\s?hash|auth\s?hash)\b",
        re.I,
    ): "CREDENTIAL_HASH",

    # --- Contact ---------------------------------------------------------
    re.compile(
        r"\b(phone|mobile|cell|tel|telephone|msisdn|phone\s?number)\b",
        re.I,
    ): "PHONE",
    re.compile(r"\b(e\s?mail|email|email\s?address)\b", re.I): "EMAIL",

    # --- Country IDs -----------------------------------------------------
    re.compile(r"\b(aadhaar|uid)\b", re.I): "AADHAAR_IN",
    re.compile(r"\b(pan\s?no|pan\s?number)\b", re.I): "PAN_IN",
    re.compile(r"\bcpf\b", re.I): "CPF_BR",
    re.compile(r"\bcurp\b", re.I): "CURP_MX",
    re.compile(r"\b(nhs|nhs\s?number)\b", re.I): "NHS_GB",
    re.compile(r"\b(nino|ni\s?number)\b", re.I): "NINO_GB",
    re.compile(r"\bpesel\b", re.I): "PESEL_PL",
    re.compile(r"\bbsn\b", re.I): "BSN_NL",
    re.compile(r"\b(personnummer|personal\s?number)\b", re.I): "PERSONNUMMER_SE",
    re.compile(r"\bnric\b", re.I): "NRIC_SG",
    re.compile(r"\b(codice\s?fiscale)\b", re.I): "CODICE_FISCALE_IT",
    re.compile(r"\b(dni|nie)\b", re.I): "DNI_ES",
    re.compile(r"\bnir\b", re.I): "NIR_FR",

    # --- Names / addresses / locations ----------------------------------
    re.compile(
        r"\b(first\s?name|last\s?name|full\s?name|surname|patient\s?name|customer\s?name)\b",
        re.I,
    ): "PERSON_NAME",
    re.compile(r"\b(address|street|addr\s?line)\b", re.I): "ADDRESS",
    re.compile(r"\b(zip|zipcode|postcode|postal\s?code)\b", re.I): "POSTAL_CODE",
    re.compile(r"\b(latitude|longitude|lat\s?lon|geo)\b", re.I): "GEO_COORD",
    re.compile(r"\b(ip|ip\s?addr|client\s?ip|remote\s?ip)\b", re.I): "IPV4",
    re.compile(r"\b(country|country\s?code|iso\s?country)\b", re.I): "COUNTRY_CODE",
}


def _normalise(column_name: str) -> str:
    """Lower-case + collapse underscores to spaces for boundary-aware matching.

    Python's ``\\b`` treats ``_`` as a word character, so ``\\bssn\\b``
    fails to match the ``ssn`` segment of ``employee_ssn``.  Replacing ``_``
    with spaces before regex matching lets the same ``\\b`` anchors do the
    right thing for both ``ssn`` and ``employee_ssn``.
    """
    return column_name.strip().strip("_ ").replace("_", " ").lower()


def name_prior(column_name: str) -> Optional[str]:
    """Return the PII type implied by *column_name* alone, else ``None``.

    Match strategy
    --------------
    * Lower-cased; underscores are replaced with spaces so ``\\b`` anchors
      see real word boundaries.
    * Each prior regex is tried in dictionary insertion order.  More-specific
      keys (e.g. ``pan_no``) are listed before less-specific ones in
      ``COLUMN_NAME_PRIORS``, so the first hit wins.

    The function is intentionally cheap: regexes are pre-compiled at import,
    no fuzzy matching is performed.
    """
    if not column_name:
        return None
    normalised = _normalise(column_name)
    if not normalised:
        return None
    for pattern, pii_type in COLUMN_NAME_PRIORS.items():
        if pattern.search(normalised):
            return pii_type
    return None


# Negative priors — when a column NAME contains one of these tokens, the
# regex/validator score for the corresponding pii_type is dampened (multiplied
# by a configurable factor, default 0.2).  This catches cases like "phone"
# columns also matching the bare-digit PESEL_PL regex, or "title"/"name"
# columns lighting up SWIFT_BIC / VAT_EU.
_NEGATIVE_PRIORS: dict[str, list[str]] = {
    "PESEL_PL": ["phone", "phone_number", "mobile", "cell", "fax"],
    "SWIFT_BIC": ["title", "name", "description", "subject"],
    "VAT_EU": ["title", "name", "description", "subject"],
    "CC_NUMBER": [
        "account_number",
        "sales_order_number",
        "po_number",
        "invoice_number",
    ],
    # PASSPORT_GB and BSN_NL share the bare \b\d{9}\b regex; same negative
    # tokens as PESEL_PL apply.
    "PASSPORT_GB": ["phone", "phone_number", "mobile", "cell", "fax"],
    "BSN_NL": ["phone", "phone_number", "mobile", "cell", "fax"],
    # AADHAAR_IN's regex is 12 contiguous digits — the same shape as a
    # country-coded phone number stored without ``+``.  Without this
    # negative prior, ``person_phone.phone_number`` columns on every
    # international dataset get a spurious ``DPDPA`` regulation tag.
    "AADHAAR_IN": ["phone", "phone_number", "mobile", "cell", "fax", "msisdn"],
    # CARD_HOLDER_NAME's two-token Capitalized-Word regex matches any
    # multi-word proper noun — city names, department names, country
    # names, address-line-1.  The positive ``card_holder`` /
    # ``cardholder`` / ``name_on_card`` prior is still the primary gate;
    # this just dampens the score on common false-positive shapes.
    "CARD_HOLDER_NAME": [
        "address", "address_line", "city", "state", "country",
        "region", "department", "group_name", "title",
        "description", "name", "category",
    ],
}


def negative_prior_match(column_name: str, pii_type: str) -> bool:
    """Return True if *column_name* contains a negative-prior token for
    *pii_type*.

    Tokens are matched as substrings of the normalised column name (lower-case,
    underscores collapsed to spaces) — this matches both ``phone`` and
    ``phone_number`` against the ``phone`` token.
    """
    tokens = _NEGATIVE_PRIORS.get(pii_type)
    if not tokens or not column_name:
        return False
    norm = _normalise(column_name)
    if not norm:
        return False
    for tok in tokens:
        # Normalise the token the same way to match e.g. ``phone_number``.
        norm_tok = tok.replace("_", " ").lower()
        if norm_tok in norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Structural-key name suppression
# ---------------------------------------------------------------------------
#
# Surrogate-key columns (``id``, ``foo_id``, ``user_uuid``, ``order_pk``, …)
# carry opaque values — UUIDs, hashes, dense integers — that frequently
# match high-entropy PII patterns (``API_KEY``, ``PHONE_US`` digit-shape,
# ``PESEL_PL``, ``SWIFT_BIC``) by accident.  When the only evidence is an
# unvalidated regex hit on such a column and the column name doesn't actually
# imply the matched PII type, the finding is almost always a false positive.
#
# The PII scanner consults :func:`is_structural_key_name` to drop those
# findings; the heuristic is name-shape only, so it never silences a
# validator-confirmed match (Luhn / checksum / NER) or a positive
# name-prior signal.

# Pointer-typed names: pure surrogate-key references (FK / PK pointers).
# A column with one of these shapes is structurally guaranteed to hold an
# opaque identifier — never a real API key, phone number, etc.  Findings
# without an explicit positive name-prior are suppressed.
_STRUCTURAL_POINTER_NAME_RE = re.compile(
    r"^(id|uuid|guid|oid|pk|fk|sid|gid)$|"
    r"_(id|uuid|guid|oid|pk|fk|sid|gid|ref)$",
    re.IGNORECASE,
)

# Broader "key-like" names: includes ``_key`` and ``_hash`` suffixes.  These
# are ambiguous — ``api_key``, ``password_hash``, and ``access_token_hash``
# legitimately hold credential material — so we don't suppress findings on
# them by default.  Reserved for future use.
_STRUCTURAL_KEY_NAME_RE = re.compile(
    r"^(id|uuid|guid|oid|pk|fk|sid|gid|hash|key)$|"
    r"_(id|uuid|guid|oid|pk|fk|sid|gid|ref|hash|key)$",
    re.IGNORECASE,
)


def is_structural_pointer_name(column_name: str) -> bool:
    """Return True for **pure surrogate-key pointer** column names —
    ``id``, ``foo_id``, ``user_uuid``, ``order_pk``, ``parent_fk``,
    ``vendor_ref``, ``record_oid``, …

    These columns are structurally guaranteed to carry opaque identifiers
    (UUIDs, hashes, dense integers).  A regex hit on an ``API_KEY``,
    ``PHONE_US``, ``CC_NUMBER`` etc. pattern on such a column is a false
    positive virtually 100% of the time — even when the pattern's
    "validator" (entropy / Luhn / checksum) reports a pass.

    Note: ``_key`` and ``_hash`` are *not* covered here — those names
    legitimately hold credential or hash material in many schemas
    (``api_key``, ``password_hash``).  Use :func:`is_structural_key_name`
    for the broader form when you specifically want to include them.
    """
    if not column_name:
        return False
    return bool(_STRUCTURAL_POINTER_NAME_RE.search(column_name.strip().lower()))


def is_structural_key_name(column_name: str) -> bool:
    """Broader form of :func:`is_structural_pointer_name` that also matches
    ``_key`` and ``_hash`` suffixes.  Kept for callers that want to flag
    every key-shaped column; PII suppression uses the *pointer* variant
    so genuinely-credential-bearing ``api_key`` / ``password_hash``
    columns still surface their findings."""
    if not column_name:
        return False
    return bool(_STRUCTURAL_KEY_NAME_RE.search(column_name.strip().lower()))


# Credential / password storage column names — ``password``,
# ``password_hash``, ``passwd``, ``pwd_hash``, ``user_pass``,
# ``password_digest``, ``hashed_password``, etc.  These columns hold
# bcrypt / argon2 / scrypt / PBKDF2 output (high entropy) which the
# generic ``API_KEY`` entropy regex matches gleefully — producing both
# a misleading API_KEY tag AND its inherited SOX regulation chip.
# Neither label fits a password hash: it isn't an API key, and SOX
# financial-controls scope doesn't apply to user authentication
# storage.  We suppress every matched PII type on these columns
# unless the column name positively implies that specific type
# (mirrors the structural-pointer suppression).  The CREDENTIAL_HASH
# type is the one positively-implied label, surfaced via the
# COLUMN_NAME_PRIORS table; everything else gets dropped.
_CREDENTIAL_NAME_RE = re.compile(
    # Core credential-storage shapes — password / hash / salt / digest /
    # iterations / kdf parameters.  Salt + iterations live alongside
    # the hash in PBKDF2 / scrypt schemes; tagging only the hash and
    # leaving the salt to be mis-classified as PHONE_US (which the
    # pipeline did before this pass) was the original bug.
    r"^(password|passwd|pwd|user_?pass|hashed_password|"
    r"password_hash|password_digest|passwd_hash|pwd_hash|"
    r"password_salt|passwd_salt|pwd_salt|salt|"
    r"password_iterations|pbkdf2_iterations|kdf_iterations|"
    r"secret_hash|credentials?_hash|auth_hash)$|"
    r"_(password|passwd|password_hash|password_digest|pwd_hash|"
    r"password_salt|password_iterations)$",
    re.IGNORECASE,
)


def is_credential_name(column_name: str) -> bool:
    """Return True for password / password-hash column shapes.

    Used by :mod:`pii_scan` to suppress entropy-based / regex-based
    findings (notably ``API_KEY``, which carries a SOX regulation tag
    that is wrong for credential storage) on these columns.  The only
    finding that survives on a credential-named column is the one whose
    own ``name_prior`` matches — i.e. ``CREDENTIAL_HASH`` itself.
    """
    if not column_name:
        return False
    return bool(_CREDENTIAL_NAME_RE.search(column_name.strip().lower()))


# ---------------------------------------------------------------------------
# Free-text content names (description / comment / body / etc.)
# ---------------------------------------------------------------------------
#
# These column names indicate free-form prose: a short blob of human-written
# text that may incidentally contain PII-shaped substrings (emails, phone
# numbers, account numbers, ...) but the column itself is NOT structurally a
# PII column.  When the matched ``pii_type`` has *no* positive name-prior on
# the column, the score is dampened so noisy free-text matches don't compete
# with genuine PII columns at the persistence threshold.
#
# Caller convention (pii_score.column_pii_confidence): pass ``free_text_column``
# when the name matches this set; the dampener is suppressed automatically
# when the column ALSO has a positive name-prior for the matched type
# (e.g. ``email_note`` matching EMAIL still scores cleanly).

_FREE_TEXT_NAME_RE = re.compile(
    r"^(description|desc|comments?|notes?|body|text|content|message|"
    r"summary|subject|label|title|name|file_?name|category|status|"
    r"created_by|modified_by|updated_by|deleted_by|owner|"
    r"reason|remark|remarks|memo)$|"
    r"_(description|desc|comments?|notes?|body|text|content|message|"
    r"summary|subject|label|title|name|category|reason|remark|remarks|memo)$",
    re.IGNORECASE,
)


def is_free_text_column_name(column_name: str) -> bool:
    """Return True when *column_name* indicates a free-form prose column
    (``description``, ``comment``, ``notes``, ``body``, ``message``,
    ``title``, ``name``, ``file_name``, ``status``, ``created_by``, …).

    Free-text columns may contain *any* shape of value — including email
    addresses, phone numbers, IBANs — but the column itself is not a PII
    column structurally.  Surfacing every such match at confidence 1.0
    floods the findings table.

    Used by :func:`pii_score.column_pii_confidence` to dampen the score
    when the column name says "this is prose" and the matched ``pii_type``
    has no positive prior on the column.  When a positive prior *does*
    exist (e.g. ``email_notes`` is named for EMAIL), the dampener is
    skipped so genuine name-matched PII still surfaces.
    """
    if not column_name:
        return False
    return bool(_FREE_TEXT_NAME_RE.search(column_name.strip().lower()))


def name_prior_strength(column_name: str, pii_type: str) -> float:
    """Return the Bayesian π_name component for (column_name, pii_type).

    Per the upgrade plan:
        * 0.85 if the column name *equals* (case-insensitively, with
          underscores collapsed) one of the canonical keys for *pii_type*.
        * 0.50 if the column name *contains* a key for *pii_type* but isn't
          an exact match.
        * 0.0  otherwise.

    This deliberately ignores other types' priors — callers asking
    ``name_prior_strength('ssn', 'EMAIL')`` correctly receive 0.0.
    """
    if not column_name or not pii_type:
        return 0.0
    norm_col = _normalise(column_name)
    if not norm_col:
        return 0.0

    matched_type = name_prior(column_name)
    if matched_type != pii_type:
        return 0.0

    # Look up the matching pattern and check whether the regex consumed the
    # *entire* normalised column name — that's the "exact match" case that
    # earns 0.85.  Substring matches earn 0.50.
    for pattern, t in COLUMN_NAME_PRIORS.items():
        if t != pii_type:
            continue
        m = pattern.search(norm_col)
        if not m:
            continue
        if m.start() == 0 and m.end() == len(norm_col):
            return 0.85
        return 0.50
    return 0.0
