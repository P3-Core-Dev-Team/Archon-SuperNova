package com.archon.openmetadata.analysis.dto;

import lombok.Data;

@Data
public class RelationshipDto {
    private String sourceTableName;
    private String sourceColumnName;
    private String targetTableName;
    private String targetColumnName;
    private Float score;
    private String cardinality;
}
