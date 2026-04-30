package com.metadata.engine.be.metadata_engine_be.models.dto;

import lombok.Data;
import java.util.List;

@Data
public class CandidateResponse {
    private String message;
    private List<CandidateMatch> candidates;

    @Data
    public static class CandidateMatch {
        private String table_a;
        private String col_a;
        private String table_b;
        private String col_b;
        private Double score;
    }
}
