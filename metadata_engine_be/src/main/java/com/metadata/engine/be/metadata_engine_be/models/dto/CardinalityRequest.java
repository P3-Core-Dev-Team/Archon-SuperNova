package com.metadata.engine.be.metadata_engine_be.models.dto;

import lombok.Data;
import java.util.List;

@Data
public class CardinalityRequest {
    private ConnectionDetails connection;
    private List<CandidateResponse.CandidateMatch> candidates;
    
    @Data
    public static class ConnectionDetails {
        private String url;
        private String username;
        private String password;
    }
}
