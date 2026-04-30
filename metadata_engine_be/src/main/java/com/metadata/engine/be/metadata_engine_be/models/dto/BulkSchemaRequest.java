package com.metadata.engine.be.metadata_engine_be.models.dto;

import lombok.Data;
import java.util.List;

@Data
public class BulkSchemaRequest {
    private String schemaName;
    private List<SchemaTable> tables;
    private List<com.metadata.engine.be.metadata_engine_be.models.Relationship> schemaCrawlerRelationships;
    private List<com.metadata.engine.be.metadata_engine_be.models.Relationship> mlRelationships;
}
