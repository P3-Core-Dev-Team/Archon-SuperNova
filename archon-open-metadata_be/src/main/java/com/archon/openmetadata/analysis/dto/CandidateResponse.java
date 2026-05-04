package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class CandidateResponse {
    private List<RelationshipDto> candidates;
    private Float minValue;
    private Float maxValue;
}
