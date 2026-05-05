package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class BulkSchemaRequest {
    private String schemaName;
    private List<SchemaTable> tables;
    private List<RelationshipDto> existingRelationships;
    private Float minValue;
    private Float maxValue;
}
