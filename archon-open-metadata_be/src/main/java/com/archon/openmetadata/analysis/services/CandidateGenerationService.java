package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.BulkSchemaRequest;
import com.archon.openmetadata.analysis.dto.CandidateResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;

@Service
@RequiredArgsConstructor
@Slf4j
public class CandidateGenerationService {
    private final RestTemplate restTemplate = new RestTemplate();
    @Value("${app.ml.api.url}")
    private String pythonApiBase;

    public CandidateResponse generateCandidates(BulkSchemaRequest chunk) {
        log.info("Sending batch of {} tables for Candidate Generation...", chunk.getTables().size());
        try {
            return restTemplate.postForObject(
                    pythonApiBase + "/stage-candidate-detection",
                    chunk,
                    CandidateResponse.class);
        } catch (Exception e) {
            log.warn("FastAPI Offline or Error in generateCandidates: {}", e.getMessage());
            return null;
        }
    }
}
