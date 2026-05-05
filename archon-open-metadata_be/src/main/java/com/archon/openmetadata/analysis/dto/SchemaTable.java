package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class SchemaTable {
    private String tableName;
    private List<SchemaColumn> columns;
}
