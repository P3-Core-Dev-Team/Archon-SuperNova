"""
test_type_class.py — Table-driven tests for classify_pg_type and is_fk_eligible.

Pure unit tests; no DB or network required.
"""
from __future__ import annotations

import pytest

from discovery.type_class import TypeClass, classify_pg_type, is_fk_eligible


# ---------------------------------------------------------------------------
# classify_pg_type test matrix
# ---------------------------------------------------------------------------

CLASSIFY_CASES: list[tuple[str, int | None, TypeClass]] = [
    # INT_NARROW
    ("integer", None, TypeClass.INT_NARROW),
    ("smallint", None, TypeClass.INT_NARROW),
    ("int", None, TypeClass.INT_NARROW),
    ("int2", None, TypeClass.INT_NARROW),
    ("int4", None, TypeClass.INT_NARROW),

    # INT_WIDE
    ("bigint", None, TypeClass.INT_WIDE),
    ("int8", None, TypeClass.INT_WIDE),

    # UUID
    ("uuid", None, TypeClass.UUID),

    # STRING_SHORT — max_length <= 64
    ("character varying", 10, TypeClass.STRING_SHORT),
    ("character varying", 64, TypeClass.STRING_SHORT),
    ("varchar", 36, TypeClass.STRING_SHORT),  # UUID-as-varchar pattern
    ("character", 1, TypeClass.STRING_SHORT),
    ("char", 3, TypeClass.STRING_SHORT),

    # STRING_LONG — max_length > 64 or None
    ("character varying", 65, TypeClass.STRING_LONG),
    ("character varying", 255, TypeClass.STRING_LONG),
    ("character varying", None, TypeClass.STRING_LONG),  # unbounded
    ("varchar", None, TypeClass.STRING_LONG),
    ("text", None, TypeClass.STRING_LONG),
    ("text", 10, TypeClass.STRING_SHORT),   # text with explicit max_length <= 64
    ("bpchar", None, TypeClass.STRING_LONG),

    # DATE
    ("date", None, TypeClass.DATE),

    # TIMESTAMP — covers both variants
    ("timestamp without time zone", None, TypeClass.TIMESTAMP),
    ("timestamp with time zone", None, TypeClass.TIMESTAMP),
    ("timestamp", None, TypeClass.TIMESTAMP),

    # BOOL
    ("boolean", None, TypeClass.BOOL),
    ("bool", None, TypeClass.BOOL),

    # FLOAT
    ("numeric", None, TypeClass.FLOAT),
    ("decimal", None, TypeClass.FLOAT),
    ("double precision", None, TypeClass.FLOAT),
    ("float8", None, TypeClass.FLOAT),
    ("real", None, TypeClass.FLOAT),
    ("float4", None, TypeClass.FLOAT),
    ("money", None, TypeClass.FLOAT),

    # BINARY
    ("bytea", None, TypeClass.BINARY),

    # Whitespace tolerance
    ("  integer  ", None, TypeClass.INT_NARROW),

    # JSONB / JSON map to TypeClass.JSONB (added by polymorphic/jsonb-fk pass).
    ("jsonb", None, TypeClass.JSONB),
    ("json", None, TypeClass.JSONB),

    # Unknown type → STRING_LONG (safe fallback)
    ("hstore", None, TypeClass.STRING_LONG),
    ("point", None, TypeClass.STRING_LONG),
    ("inet", None, TypeClass.STRING_LONG),
]


@pytest.mark.parametrize("data_type,max_length,expected_class", CLASSIFY_CASES)
def test_classify_pg_type(
    data_type: str,
    max_length: int | None,
    expected_class: TypeClass,
) -> None:
    result = classify_pg_type(data_type, max_length)
    assert result == expected_class, (
        f"classify_pg_type({data_type!r}, {max_length}) = {result}, expected {expected_class}"
    )


# ---------------------------------------------------------------------------
# is_fk_eligible test matrix
# ---------------------------------------------------------------------------

FK_ELIGIBLE_CASES: list[tuple[TypeClass, bool]] = [
    (TypeClass.INT_NARROW, True),
    (TypeClass.INT_WIDE, True),
    (TypeClass.UUID, True),
    (TypeClass.STRING_SHORT, True),
    (TypeClass.DATE, True),
    (TypeClass.TIMESTAMP, True),
    # NOT eligible:
    (TypeClass.STRING_LONG, False),
    (TypeClass.BOOL, False),
    (TypeClass.FLOAT, False),
    (TypeClass.BINARY, False),
]


@pytest.mark.parametrize("type_class,expected_eligible", FK_ELIGIBLE_CASES)
def test_is_fk_eligible(type_class: TypeClass, expected_eligible: bool) -> None:
    result = is_fk_eligible(type_class)
    assert result == expected_eligible, (
        f"is_fk_eligible({type_class}) = {result}, expected {expected_eligible}"
    )


def test_all_type_classes_have_eligibility_defined() -> None:
    """Every TypeClass member must return a boolean from is_fk_eligible without error."""
    for tc in TypeClass:
        result = is_fk_eligible(tc)
        assert isinstance(result, bool), f"is_fk_eligible({tc}) did not return bool"
