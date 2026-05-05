package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.GraphContextRequest;
import com.archon.openmetadata.analysis.dto.GraphContextResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;

@Service
@RequiredArgsConstructor
@Slf4j
public class GraphContextService {
    private final RestTemplate restTemplate = new RestTemplate();
    @Value("${app.ml.api.url}")
    private String pythonApiBase;

    public GraphContextResponse generateContextGraph(GraphContextRequest req) {
        log.info("Sending {} relationships for ERD Context Graph Generation...", req.getRelationships().size());
        try {
            return restTemplate.postForObject(
                    pythonApiBase + "/stage-relationship-context-graph",
                    req,
                    GraphContextResponse.class);
        } catch (Exception e) {
            log.warn("FastAPI Error in generateContextGraph: {}", e.getMessage());
            return null;
        }
    }
}
