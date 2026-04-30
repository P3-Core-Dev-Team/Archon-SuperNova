package com.metadata.engine.be.metadata_engine_be.models.dto;

import com.metadata.engine.be.metadata_engine_be.models.Relationship;
import lombok.Data;
import java.util.List;

@Data
public class CardinalityResponse {
    private String message;
    private List<Relationship> relationships;
}
