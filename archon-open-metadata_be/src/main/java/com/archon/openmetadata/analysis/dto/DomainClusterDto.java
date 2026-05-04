package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class DomainClusterDto {
    private String clusterName;
    private List<String> tableNames;
}
