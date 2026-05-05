package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class CardinalityResponse {
    private List<RelationshipDto> relationships;
}
