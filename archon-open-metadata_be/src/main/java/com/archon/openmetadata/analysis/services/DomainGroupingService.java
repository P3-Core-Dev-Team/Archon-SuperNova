package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.BulkSchemaRequest;
import com.archon.openmetadata.analysis.dto.DomainGroupResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;

@Service
@RequiredArgsConstructor
@Slf4j
public class DomainGroupingService {
    private final RestTemplate restTemplate = new RestTemplate();
    @Value("${app.ml.api.url}")
    private String pythonApiBase;

    public DomainGroupResponse extractDomainGroups(BulkSchemaRequest chunk) {
        log.info("Sending batch of {} tables for Domain Vector Aggregation...", chunk.getTables().size());
        try {
            return restTemplate.postForObject(
                    pythonApiBase + "/stage-domain-grouping",
                    chunk,
                    DomainGroupResponse.class);
        } catch (Exception e) {
            log.warn("FastAPI Error in extractDomainGroups: {}", e.getMessage());
            return null;
        }
    }
}
