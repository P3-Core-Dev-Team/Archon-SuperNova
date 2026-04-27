"""IBAN generator using python-stdnum for validation."""

from __future__ import annotations

import random as stdlib_random

try:
    from stdnum import iban as stdnum_iban
    _HAS_STDNUM = True
except ImportError:
    _HAS_STDNUM = False


# IBAN formats: (country_code, bank_code_length, account_length)
# Format: (country, total_length, bban_structure_example)
_IBAN_FORMATS = [
    # (country, bban_length, description)
    ("GB", 22, "sort-code+account"),
    ("DE", 22, "BLZ+account"),
    ("FR", 27, "bank+branch+account+key"),
    ("NL", 18, "bank+account"),
    ("ES", 24, "bank+branch+ctrl+account"),
    ("IT", 27, "ctrl+bank+branch+account"),
    ("BE", 16, "bank+account+check"),
]

# Country-specific bank codes for realism
_BANK_CODES = {
    "GB": ["NWBK", "LOYD", "HBUK", "BARC", "HSBC"],
    "DE": ["10010010", "20010020", "37040044", "50010517", "76010085"],
    "FR": ["30004", "30006", "13135", "17569", "18206"],
    "NL": ["ABNA", "RABO", "INGB", "TRIO", "SNSB"],
    "ES": ["0049", "0075", "0182", "2100", "1465"],
    "IT": ["X", "A", "B", "C", "D"],  # check char
    "BE": ["539", "068", "363", "096", "240"],
}


def _compute_iban_check_digits(country: str, bban: str) -> str:
    """Compute the 2-digit check digits for an IBAN."""
    # Rearrange: BBAN + country + "00"
    rearranged = bban + country + "00"
    # Convert letters to digits: A=10, B=11, ..., Z=35
    numeric = ""
    for ch in rearranged:
        if ch.isalpha():
            numeric += str(ord(ch.upper()) - ord('A') + 10)
        else:
            numeric += ch
    check = 98 - (int(numeric) % 97)
    return f"{check:02d}"


def _generate_gb_iban(rng: stdlib_random.Random) -> str:
    bank = rng.choice(_BANK_CODES["GB"])
    sort_code = f"{rng.randint(0, 999999):06d}"
    account = f"{rng.randint(0, 99999999):08d}"
    bban = f"{bank}{sort_code}{account}"
    check = _compute_iban_check_digits("GB", bban)
    return f"GB{check}{bban}"


def _generate_de_iban(rng: stdlib_random.Random) -> str:
    blz = rng.choice(_BANK_CODES["DE"])
    account = f"{rng.randint(0, 9999999999):010d}"
    bban = f"{blz}{account}"
    check = _compute_iban_check_digits("DE", bban)
    return f"DE{check}{bban}"


def _generate_fr_iban(rng: stdlib_random.Random) -> str:
    bank = rng.choice(_BANK_CODES["FR"])
    branch = f"{rng.randint(0, 99999):05d}"
    account = f"{rng.randint(0, 99999999999):011d}"
    # French IBAN key is 2 digits
    key = f"{rng.randint(0, 99):02d}"
    bban = f"{bank}{branch}{account}{key}"
    check = _compute_iban_check_digits("FR", bban)
    return f"FR{check}{bban}"


def _generate_nl_iban(rng: stdlib_random.Random) -> str:
    bank = rng.choice(_BANK_CODES["NL"])
    account = f"{rng.randint(0, 9999999999):010d}"
    bban = f"{bank}{account}"
    check = _compute_iban_check_digits("NL", bban)
    return f"NL{check}{bban}"


def _generate_be_iban(rng: stdlib_random.Random) -> str:
    bank = rng.choice(_BANK_CODES["BE"])
    account = f"{rng.randint(0, 9999999):07d}"
    check_digits = f"{rng.randint(1, 97):02d}"
    bban = f"{bank}{account}{check_digits}"
    check = _compute_iban_check_digits("BE", bban)
    return f"BE{check}{bban}"


_GENERATORS = {
    "GB": _generate_gb_iban,
    "DE": _generate_de_iban,
    "FR": _generate_fr_iban,
    "NL": _generate_nl_iban,
    "BE": _generate_be_iban,
}

_COUNTRY_CHOICES = list(_GENERATORS.keys())


def generate_iban(rng: stdlib_random.Random | None = None) -> str:
    """
    Generate a checksum-valid IBAN string.
    Uses stdnum for validation if available; otherwise computes internally.
    """
    if rng is None:
        rng = stdlib_random.Random()

    country = rng.choice(_COUNTRY_CHOICES)
    gen_fn = _GENERATORS[country]

    # Try up to 20 times (some combinations fail mod-97 check due to bban structure)
    for _ in range(20):
        candidate = gen_fn(rng)
        if _HAS_STDNUM:
            try:
                if stdnum_iban.is_valid(candidate):
                    return candidate
            except Exception:
                pass
        else:
            # Validate our own check digit
            if _validate_iban_checksum(candidate):
                return candidate
    # Fallback: return a known-valid DE IBAN structure
    return _generate_de_iban(rng)


def _validate_iban_checksum(iban: str) -> bool:
    """Validate IBAN checksum via mod-97."""
    iban = iban.replace(" ", "").upper()
    if len(iban) < 5:
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = ""
    for ch in rearranged:
        if ch.isalpha():
            numeric += str(ord(ch) - ord('A') + 10)
        else:
            numeric += ch
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


def generate_iban_batch(n: int, rng: stdlib_random.Random | None = None) -> list[str]:
    """Generate n valid IBAN strings."""
    if rng is None:
        rng = stdlib_random.Random()
    return [generate_iban(rng) for _ in range(n)]


def iban_valid(iban: str) -> bool:
    """Return True if iban passes checksum validation."""
    if _HAS_STDNUM:
        try:
            return stdnum_iban.is_valid(iban)
        except Exception:
            return False
    return _validate_iban_checksum(iban)
