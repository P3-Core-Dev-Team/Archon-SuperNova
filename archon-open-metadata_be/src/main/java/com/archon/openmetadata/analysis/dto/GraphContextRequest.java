package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class GraphContextRequest {
    private List<RelationshipDto> relationships;
    private List<DomainClusterDto> clusters;
    private Float minValue;
    private Float maxValue;
}
