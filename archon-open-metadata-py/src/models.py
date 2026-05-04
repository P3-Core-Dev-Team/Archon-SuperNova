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
