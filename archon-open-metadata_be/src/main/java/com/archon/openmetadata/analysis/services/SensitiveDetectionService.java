package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.BulkSchemaRequest;
import com.archon.openmetadata.analysis.dto.SensitiveResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;

@Service
@RequiredArgsConstructor
@Slf4j
public class SensitiveDetectionService {
    private final RestTemplate restTemplate = new RestTemplate();
    @Value("${app.ml.api.url}")
    private String pythonApiBase;

    public SensitiveResponse detectSensitiveEntities(BulkSchemaRequest chunk) {
        log.info("Sending batch of {} tables for SpaCy PII Classification...", chunk.getTables().size());
        try {
            return restTemplate.postForObject(
                    pythonApiBase + "/stage-sensitive-detection",
                    chunk,
                    SensitiveResponse.class);
        } catch (Exception e) {
            log.warn("FastAPI Error in detectSensitiveEntities: {}", e.getMessage());
            return null;
        }
    }
}
