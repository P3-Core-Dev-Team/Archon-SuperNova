package com.metadata.engine.be.metadata_engine_be.models.dto;

import lombok.Data;
import java.util.List;
import java.util.Map;

@Data
public class GraphContextResponse {
    private String message;
    private GraphData graph;
    
    @Data
    public static class GraphData {
        private List<Map<String, Object>> nodes;
        private List<Map<String, Object>> links;
    }
}
