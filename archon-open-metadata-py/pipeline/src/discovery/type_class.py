"""
type_class.py — Postgres data_type → TypeClass classifier.

TypeClass is used downstream for:
  - col_inventory.type_class storage
  - is_fk_eligible determination (which columns are worth FK candidate search)
"""
from __future__ import annotations

from enum import Enum


class TypeClass(str, Enum):
    """Coarse type classification for discovery purposes."""

    INT_NARROW = "INT_NARROW"   # integer, smallint
    INT_WIDE = "INT_WIDE"       # bigint
    UUID = "UUID"               # uuid
    STRING_SHORT = "STRING_SHORT"  # varchar/text with max_length <= 64
    STRING_LONG = "STRING_LONG"    # varchar/text with max_length > 64, or no length
    DATE = "DATE"               # date
    TIMESTAMP = "TIMESTAMP"     # timestamp with/without time zone
    BOOL = "BOOL"               # boolean
    FLOAT = "FLOAT"             # numeric, double precision, real
    BINARY = "BINARY"           # bytea
    JSONB = "JSONB"             # jsonb / json (extracted as VARCHAR text in parquet)


# Canonical data_type strings Postgres returns from information_schema.columns
_INT_NARROW = frozenset({"integer", "smallint", "int", "int2", "int4"})
_INT_WIDE = frozenset({"bigint", "int8"})
_UUID = frozenset({"uuid"})
_STRING_TYPES = frozenset(
    {"character varying", "varchar", "character", "char", "text", "bpchar"}
)
_DATE = frozenset({"date"})
_BOOL = frozenset({"boolean", "bool"})
_FLOAT = frozenset(
    {"numeric", "decimal", "double precision", "float8", "real", "float4", "money"}
)
_BINARY = frozenset({"bytea"})
_JSONB = frozenset({"jsonb", "json"})

# String threshold
_STRING_SHORT_MAX_LENGTH = 64


def classify_pg_type(data_type: str, max_length: int | None) -> TypeClass:
    """
    Classify a Postgres column's data_type (from information_schema.columns)
    into a TypeClass bucket.

    Parameters
    ----------
    data_type:
        Lowercased data_type string from information_schema.columns.
        E.g. "integer", "character varying", "timestamp without time zone".
    max_length:
        character_maximum_length from information_schema.columns, or None.
        Only meaningful for string types.

    Returns
    -------
    TypeClass

    Notes
    -----
    - For text/varchar/char with no max_length (or max_length > 64) → STRING_LONG.
    - timestamp* matched via startswith to cover both
      "timestamp without time zone" and "timestamp with time zone".
    """
    dt = data_type.strip().lower()

    if dt in _INT_NARROW:
        return TypeClass.INT_NARROW
    if dt in _INT_WIDE:
        return TypeClass.INT_WIDE
    if dt in _UUID:
        return TypeClass.UUID
    if dt in _BOOL:
        return TypeClass.BOOL
    if dt in _FLOAT:
        return TypeClass.FLOAT
    if dt in _BINARY:
        return TypeClass.BINARY
    if dt in _JSONB:
        return TypeClass.JSONB
    if dt in _DATE:
        return TypeClass.DATE
    if dt.startswith("timestamp"):
        return TypeClass.TIMESTAMP
    if dt in _STRING_TYPES:
        # NULL max_length (text, unbounded varchar) → LONG
        if max_length is not None and max_length <= _STRING_SHORT_MAX_LENGTH:
            return TypeClass.STRING_SHORT
        return TypeClass.STRING_LONG

    # Fallback — treat unknowns as STRING_LONG to avoid accidentally making them
    # FK-eligible and cluttering the candidate set.
    return TypeClass.STRING_LONG


def is_fk_eligible(type_class: TypeClass) -> bool:
    """
    Returns True iff columns of this TypeClass should participate in FK candidate
    search.

    Excluded because they produce meaningless containment signal:
      - BOOL: only 2-3 distinct values, extreme cardinality ratios
      - FLOAT: floating-point equality is unreliable
      - BINARY: bytea blobs are never FKs in practice
      - STRING_LONG: long text rarely carries FK relationships
    """
    return type_class not in {
        TypeClass.BOOL,
        TypeClass.FLOAT,
        TypeClass.BINARY,
        TypeClass.STRING_LONG,
        TypeClass.JSONB,
    }
