package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.CardinalityRequest;
import com.archon.openmetadata.analysis.dto.CardinalityResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;

@Service
@RequiredArgsConstructor
@Slf4j
public class CardinalityResolutionService {
    private final RestTemplate restTemplate = new RestTemplate();
    @Value("${app.ml.api.url}")
    private String pythonApiBase;

    public CardinalityResponse resolveCardinalities(CardinalityRequest req) {
        if (req.getCandidates() == null || req.getCandidates().isEmpty()) {
            return null;
        }
        log.info("Sending {} relationships for Cardinality Resolution...", req.getCandidates().size());
        try {
            return restTemplate.postForObject(
                    pythonApiBase + "/stage-cardinality",
                    req,
                    CardinalityResponse.class);
        } catch (Exception e) {
            log.warn("FastAPI Error in resolveCardinalities: {}", e.getMessage());
            return null;
        }
    }
}
