package com.archon.openmetadata.analysis.services;

import com.archon.openmetadata.analysis.dto.BulkSchemaRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;

import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
@Slf4j
public class EntityClassificationService {
    private final RestTemplate restTemplate = new RestTemplate();
    @Value("${app.ml.api.url}")
    private String pythonApiBase;

    @SuppressWarnings("unchecked")
    public List<Map<String, String>> classifyEntities(BulkSchemaRequest chunk) {
        log.info("Sending batch of {} tables for Entity Classification...", chunk.getTables().size());
        try {
            Map<String, Object> res = restTemplate.postForObject(
                    pythonApiBase + "/stage-entity-classification",
                    chunk,
                    Map.class);
            if (res != null && res.containsKey("classifications")) {
                return (List<Map<String, String>>) res.get("classifications");
            }
            return null;
        } catch (Exception e) {
            log.warn("FastAPI Error in classifyEntities: {}", e.getMessage());
            return null;
        }
    }
}
