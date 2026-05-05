package com.archon.openmetadata.analysis.dto;

import lombok.Data;

@Data
public class SchemaColumn {
    private String columnName;
    private String dataType;
    private Integer length;
    private boolean isPrimaryKey;
}
