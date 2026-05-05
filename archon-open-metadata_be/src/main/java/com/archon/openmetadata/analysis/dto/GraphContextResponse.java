package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class GraphContextResponse {
    private GraphDto graph;

    @Data
    public static class GraphDto {
        private List<String> nodes;
        private List<String> edges;
    }
}
