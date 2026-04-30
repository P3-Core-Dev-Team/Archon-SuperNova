package com.metadata.engine.be.metadata_engine_be.models.dto;

import lombok.Data;
import java.util.List;

@Data
public class SchemaTable {
    private String tableName;
    private List<SchemaColumn> columns;
}
