package com.metadata.engine.be.metadata_engine_be.models.dto;

import com.metadata.engine.be.metadata_engine_be.models.Relationship;
import com.metadata.engine.be.metadata_engine_be.models.DomainGroup;
import lombok.Data;
import java.util.List;

@Data
public class GraphContextRequest {
    private List<Relationship> relationships;
    private List<DomainGroup> clusters;
}
