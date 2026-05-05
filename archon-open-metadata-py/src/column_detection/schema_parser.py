from src.models import SchemaTable

class ColumnSchemaParser:
    """
    Parses and standardizes incoming database schemas, ensuring datatypes
    and column lengths are normalized for downstream NLP engines.
    """
    @staticmethod
    def parse_schema(tables: list[SchemaTable]) -> list[SchemaTable]:
        for t in tables:
            for c in t.columns:
                c.dataType = str(c.dataType).upper().strip()
                c.columnName = str(c.columnName).strip()
        return tables
