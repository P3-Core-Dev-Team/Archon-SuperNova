from pydantic import BaseModel

class SchemaColumn(BaseModel):
    columnName: str
    dataType: str
    length: int | None = None

class SchemaTable(BaseModel):
    tableName: str
    columns: list[SchemaColumn]

class ConnectionDetails(BaseModel):
    url: str
    username: str
    password: str
    databaseName: str

class DiscoveryJobRequest(BaseModel):
    label: str
    db_type: str = "postgres"
    host: str
    port: int = 5432
    database: str
    user: str
    password: str
    schema_name: str

class BulkSchemaRequest(BaseModel):
    schemaName: str | None = None
    tables: list[SchemaTable]
    schemaCrawlerRelationships: list[dict] | None = None
    mlRelationships: list[dict] | None = None
    minValue: float | None = None
    maxValue: float | None = None

class CandidateResponse(BaseModel):
    candidates: list[dict]
    minValue: float | None = None
    maxValue: float | None = None

class CardinalityRequest(BaseModel):
    connection: ConnectionDetails | None = None
    candidates: list[dict]
    minValue: float | None = None
    maxValue: float | None = None

class ContextGraphRequest(BaseModel):
    relationships: list[dict]
    clusters: list[dict]
    minValue: float | None = None
    maxValue: float | None = None


# === data_based requests =================================================
# Inventory + relationships rows fed into the schema-pattern detectors and
# the data-quality classifier.  Fields are intentionally permissive
# (typed as ``dict``) so the open-metadata BE / SuperNova UI can feed
# whatever inventory shape they have without forcing a schema upgrade
# on every change to the discovery pipeline.

class SchemaInsightRequest(BaseModel):
    columns: list[dict]                 # rows with at minimum table, column, is_pk, data_type
    edges: list[dict]                   # rows with at minimum from, to, label
    tableNames: list[str] | None = None # optional override; derived from columns if absent


class DataQualityRequest(BaseModel):
    rows: list[dict]                    # ColumnMetrics-shaped dicts
    nullThreshold: float = 0.50
    lowCardFloor: int = 5
    lowCardMinRows: int = 1000


class TableNameListRequest(BaseModel):
    """Used by exclusion-filter and known-schema fingerprint endpoints."""
    tableNames: list[str]


class ColumnTypeBatchRequest(BaseModel):
    """Each row carries column_id (echoed back), data_type, max_length."""
    columns: list[dict]


class NamePairsRequest(BaseModel):
    """Each pair is ``{a, b}``."""
    pairs: list[dict]


class PiiScanRequest(BaseModel):
    """Each column row: ``{column_name, values, type_class?}``."""
    columns: list[dict]
    enableNer: bool = False
    maxExamples: int = 3


class CardBrandRequest(BaseModel):
    pan: str


class CardinalityFilterRequest(BaseModel):
    relationships: list[dict]
    confidenceFloor: float = 0.85


class CardinalityRefineRequest(BaseModel):
    """Each row: ``{rel: {...}, total_rows, distinct_count}``."""
    rows: list[dict]


class CompositeFkRequest(BaseModel):
    """List of single-column FK candidate rows that already share
    a (child_table, parent_table) pair."""
    singles: list[dict]
    maxArity: int = 3
    minSinglesContainment: float = 0.95
    minNameSimilarityFloor: float = 0.5


class PolymorphicDetectRequest(BaseModel):
    columns: list[dict]


class PolymorphicMatchRequest(BaseModel):
    typeColumnValues: list[str]
    candidateParents: list[str]


class ClusterRequest(BaseModel):
    schemaName: str
    tables: list[dict]
    columns: list[dict]
    edges: list[dict]
    piiFindings: list[dict] | None = None
    confidenceFloor: float = 0.7
    seed: int = 42
    semanticMergeEnabled: bool = True
    semanticMergeThreshold: float = 0.65
    semanticLabelEnabled: bool = True
    semanticLabelThreshold: float = 0.55
