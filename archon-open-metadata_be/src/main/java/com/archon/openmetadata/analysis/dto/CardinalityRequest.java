package com.archon.openmetadata.analysis.dto;

import java.util.List;
import lombok.Data;

@Data
public class CardinalityRequest {
    private ConnectionDetails connection;
    private List<RelationshipDto> candidates;
    private Float minValue;
    private Float maxValue;

    @Data
    public static class ConnectionDetails {
        private String url;
        private String username;
        private String password;
    }
}
