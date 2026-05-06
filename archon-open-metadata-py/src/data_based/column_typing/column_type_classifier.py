from enum import Enum
from typing import Optional


class TypeClass(str, Enum):
    """Coarse type classification used downstream for FK eligibility."""

    INT_NARROW = "INT_NARROW"
    INT_WIDE = "INT_WIDE"
    UUID = "UUID"
    STRING_SHORT = "STRING_SHORT"
    STRING_LONG = "STRING_LONG"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    BOOL = "BOOL"
    FLOAT = "FLOAT"
    BINARY = "BINARY"
    JSONB = "JSONB"


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

_STRING_SHORT_MAX_LENGTH = 64


class ColumnTypeClassifier:
    """
    Stage 9: Classifies a Postgres data_type string into a coarse
    ``TypeClass`` bucket and exposes the FK-eligibility predicate used
    by the candidate generator (BOOL/FLOAT/BINARY/STRING_LONG/JSONB
    are excluded — they produce meaningless containment signal).
    """

    @staticmethod
    def classify_pg_type(data_type: str, max_length: Optional[int]) -> str:
        dt = (data_type or "").strip().lower()
        if dt in _INT_NARROW: return TypeClass.INT_NARROW.value
        if dt in _INT_WIDE: return TypeClass.INT_WIDE.value
        if dt in _UUID: return TypeClass.UUID.value
        if dt in _BOOL: return TypeClass.BOOL.value
        if dt in _FLOAT: return TypeClass.FLOAT.value
        if dt in _BINARY: return TypeClass.BINARY.value
        if dt in _JSONB: return TypeClass.JSONB.value
        if dt in _DATE: return TypeClass.DATE.value
        if dt.startswith("timestamp"): return TypeClass.TIMESTAMP.value
        if dt in _STRING_TYPES:
            if max_length is not None and max_length <= _STRING_SHORT_MAX_LENGTH:
                return TypeClass.STRING_SHORT.value
            return TypeClass.STRING_LONG.value
        return TypeClass.STRING_LONG.value

    @staticmethod
    def is_fk_eligible(type_class: str) -> bool:
        return type_class not in {
            TypeClass.BOOL.value,
            TypeClass.FLOAT.value,
            TypeClass.BINARY.value,
            TypeClass.STRING_LONG.value,
            TypeClass.JSONB.value,
        }

    @staticmethod
    def classify_batch(columns: list[dict]) -> list[dict]:
        """Bulk: each input row is ``{column_id, data_type, max_length}``;
        each output row adds ``type_class`` and ``fk_eligible``."""
        out: list[dict] = []
        for c in columns:
            tc = ColumnTypeClassifier.classify_pg_type(
                str(c.get("data_type", "")),
                c.get("max_length"),
            )
            out.append({
                **c,
                "type_class": tc,
                "fk_eligible": ColumnTypeClassifier.is_fk_eligible(tc),
            })
        return out
