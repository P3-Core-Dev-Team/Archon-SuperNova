package com.archon.openmetadata.analysis.dto;

import lombok.Data;

@Data
public class SensitiveColumnDto {
    private String transientTableName;
    private String columnName;
    private String piiType;
    private Float confidenceScore;
}
