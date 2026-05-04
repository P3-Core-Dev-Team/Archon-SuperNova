package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.CandidateResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;

@Service
@RequiredArgsConstructor
@Slf4j
public class SemanticScoringService {
    private final RestTemplate restTemplate = new RestTemplate();
    @Value("${app.ml.api.url}")
    private String pythonApiBase;

    public CandidateResponse evaluateRelationships(CandidateResponse candidates) {
        if (candidates == null || candidates.getCandidates() == null || candidates.getCandidates().isEmpty()) {
            return null;
        }
        log.info("Sending {} candidates for Semantic Scoring...", candidates.getCandidates().size());
        try {
            return restTemplate.postForObject(
                    pythonApiBase + "/stage-relationship-scoring",
                    candidates,
                    CandidateResponse.class);
        } catch (Exception e) {
            log.warn("FastAPI Error in evaluateRelationships: {}", e.getMessage());
            return null;
        }
    }
}
