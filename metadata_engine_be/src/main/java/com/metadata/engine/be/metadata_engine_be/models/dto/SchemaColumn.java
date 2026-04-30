package com.metadata.engine.be.metadata_engine_be.models.dto;

import lombok.Data;

@Data
public class SchemaColumn {
    private String columnName;
    private String dataType;
    private Integer length;
    private boolean primaryKey;
}
