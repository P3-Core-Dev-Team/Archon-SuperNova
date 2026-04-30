package com.metadata.engine.be.metadata_engine_be.services;

import com.metadata.engine.be.metadata_engine_be.models.DataConnection;
import com.metadata.engine.be.metadata_engine_be.models.dto.BulkSchemaRequest;
import com.metadata.engine.be.metadata_engine_be.models.dto.CandidateResponse;
import com.metadata.engine.be.metadata_engine_be.models.dto.CardinalityRequest;
import com.metadata.engine.be.metadata_engine_be.models.dto.CardinalityResponse;
import com.metadata.engine.be.metadata_engine_be.models.dto.SensitiveResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

@Service
@RequiredArgsConstructor
public class PythonMlIntegrationService {
    private final SseBroadcasterService sse;
    private final RestTemplate restTemplate = new RestTemplate();

    public CandidateResponse generateCandidates(Long jobId, BulkSchemaRequest schema) {
        sse.sendEvent(jobId, Map.of("stage", "[MATCH]", "msg",
                "Serializing graph space to external FastAPI Analytics Node...", "cls", "lm"));
        try {
            CandidateResponse res = restTemplate.postForObject(
                    "http://127.0.0.1:7000/api/v1/stage-candidate-detection",
                    schema,
                    CandidateResponse.class);
            int matchFound = res != null && res.getCandidates() != null ? res.getCandidates().size() : 0;
            sse.sendEvent(jobId, Map.of("stage", "[MATCH]", "msg",
                    "Stage 1 candidate generation complete. Found " + matchFound + " pairs.", "cls", "lok"));
            return res;
        } catch (Exception e) {
            sse.sendEvent(jobId, Map.of("stage", "[MATCH]", "msg",
                    "FastAPI Node Offline. Candidate match simulated locally.", "cls", "lwarn"));
            return null;
        }
    }

    public CandidateResponse evaluateRelationships(Long jobId, CandidateResponse candidates) {
        if (candidates == null)
            return null;
        sse.sendEvent(jobId, Map.of("stage", "[MATCH]", "msg",
                "Delegating candidates to Stage 2 Semantic / Context Scoring...", "cls", "lm"));
        try {
            CandidateResponse res = restTemplate.postForObject(
                    "http://127.0.0.1:7000/api/v1/stage-relationship-scoring",
                    candidates,
                    CandidateResponse.class);
            int scoredFound = res != null && res.getCandidates() != null ? res.getCandidates().size() : 0;
            sse.sendEvent(jobId, Map.of("stage", "[MATCH]", "msg",
                    "Stage 2 RapidFuzz/spaCy execution finished. Accepted pairs: " + scoredFound, "cls", "lok"));
            return res;
        } catch (Exception e) {
            sse.sendEvent(jobId, Map.of("stage", "[MATCH]", "msg", "FastAPI Stage 2 Scoring failed.", "cls", "lwarn"));
            return null;
        }
    }

    public CardinalityResponse resolveCardinalities(Long jobId, CandidateResponse scoredCandidates,
            DataConnection connection) {
        sse.sendEvent(jobId,
                Map.of("stage", "[CARD]", "msg", "Booting sampling queries for Cardinality logic", "cls", "lm"));
        if (scoredCandidates == null || scoredCandidates.getCandidates() == null
                || scoredCandidates.getCandidates().isEmpty()) {
            sse.sendEvent(jobId,
                    Map.of("stage", "[CARD]", "msg", "No candidates available for cardinality.", "cls", "lwarn"));
            return null;
        }
        try {
            CardinalityRequest req = new CardinalityRequest();
            CardinalityRequest.ConnectionDetails cd = new CardinalityRequest.ConnectionDetails();
            cd.setUrl(connection.getUrl());
            cd.setUsername(connection.getUsername());
            cd.setPassword(connection.getPassword());
            req.setConnection(cd);
            req.setCandidates(scoredCandidates.getCandidates());

            CardinalityResponse res = restTemplate.postForObject(
                    "http://127.0.0.1:7000/api/v1/stage-cardinality",
                    req,
                    CardinalityResponse.class);
            int count = res != null && res.getRelationships() != null ? res.getRelationships().size() : 0;
            sse.sendEvent(jobId, Map.of("stage", "[CARD]", "msg",
                    "Analyzed row contexts successfully for " + count + " bindings.", "cls", "lok"));
            return res;
        } catch (Exception e) {
            sse.sendEvent(jobId,
                    Map.of("stage", "[ERROR]", "msg", "Cardinality engine error: " + e.getMessage(), "cls", "lwarn"));
            return null;
        }
    }

    public SensitiveResponse detectSensitiveEntities(Long jobId, BulkSchemaRequest schema) {
        sse.sendEvent(jobId, Map.of("stage", "[PII]", "msg", "Presidio / spaCy text classification pipeline engaged...",
                "cls", "lm"));
        try {
            SensitiveResponse res = restTemplate.postForObject(
                    "http://127.0.0.1:7000/api/v1/stage-sensitive-detection",
                    schema,
                    SensitiveResponse.class);
            if (res != null && res.getSensitive_columns() != null && !res.getSensitive_columns().isEmpty()) {
                sse.sendEvent(jobId, Map.of("stage", "[PII]", "msg", "Flagged " + res.getSensitive_columns().size()
                        + " sensitive entities (PII/PHI) across all columns.", "cls", "lok"));
            } else {
                sse.sendEvent(jobId,
                        Map.of("stage", "[PII]", "msg", "No high-risk PHI entities detected", "cls", "lok"));
            }
            return res;
        } catch (Exception e) {
            sse.sendEvent(jobId, Map.of("stage", "[ERROR]", "msg",
                    "Presidio Text extraction pipeline failed: " + e.getMessage(), "cls", "lwarn"));
            return null;
        }
    }

    public com.metadata.engine.be.metadata_engine_be.models.dto.DomainGroupResponse extractDomainGroups(Long jobId,
            BulkSchemaRequest schema) {
        sse.sendEvent(jobId,
                Map.of("stage", "[CLUST]", "msg", "Sentence-Transformers grouping context...", "cls", "lm"));
        try {
            com.metadata.engine.be.metadata_engine_be.models.dto.DomainGroupResponse res = restTemplate.postForObject(
                    "http://127.0.0.1:7000/api/v1/stage-domain-grouping",
                    schema,
                    com.metadata.engine.be.metadata_engine_be.models.dto.DomainGroupResponse.class);
            if (res != null && res.getClusters() != null && !res.getClusters().isEmpty()) {
                sse.sendEvent(jobId, Map.of("stage", "[CLUST]", "msg",
                        "DBSCAN reduced to " + res.getClusters().size() + " semantic subsets", "cls", "lok"));
            } else {
                sse.sendEvent(jobId, Map.of("stage", "[CLUST]", "msg", "No clusters found natively", "cls", "lwarn"));
            }
            return res;
        } catch (Exception e) {
            sse.sendEvent(jobId,
                    Map.of("stage", "[ERROR]", "msg", "Domain Grouping ML failed: " + e.getMessage(), "cls", "lwarn"));
            return null;
        }
    }

    public com.metadata.engine.be.metadata_engine_be.models.dto.GraphContextResponse generateContextGraph(Long jobId,
            com.metadata.engine.be.metadata_engine_be.models.dto.GraphContextRequest req) {
        sse.sendEvent(jobId, Map.of("stage", "[GRAPH]", "msg",
                "NetworkX mapping edges natively across domain groups...", "cls", "lm"));
        try {
            com.metadata.engine.be.metadata_engine_be.models.dto.GraphContextResponse res = restTemplate.postForObject(
                    "http://127.0.0.1:7000/api/v1/stage-relationship-context-graph",
                    req,
                    com.metadata.engine.be.metadata_engine_be.models.dto.GraphContextResponse.class);
            if (res != null && res.getGraph() != null && res.getGraph().getNodes() != null) {
                sse.sendEvent(jobId, Map.of("stage", "[GRAPH]", "msg",
                        "ER Graph bounds instantiated: " + res.getGraph().getNodes().size() + " nodes processed.",
                        "cls", "lok"));
            } else {
                sse.sendEvent(jobId,
                        Map.of("stage", "[GRAPH]", "msg", "Failed to deserialize graph output.", "cls", "lwarn"));
            }
            return res;
        } catch (Exception e) {
            sse.sendEvent(jobId, Map.of("stage", "[ERROR]", "msg", "Domain Context Graph API failed: " + e.getMessage(),
                    "cls", "lwarn"));
            return null;
        }
    }

    public java.util.List<Map<String, String>> classifyEntities(Long jobId, BulkSchemaRequest schema) {
        sse.sendEvent(jobId, Map.of("stage", "[CLASS]", "msg",
                "Extracting Graph Structural Heuristics for Table Classification...", "cls", "lm"));
        try {
            Map<String, Object> res = restTemplate.postForObject(
                    "http://127.0.0.1:7000/api/v1/stage-entity-classification",
                    schema,
                    Map.class);
            if (res != null && res.containsKey("classifications")) {
                java.util.List<Map<String, String>> classifications = (java.util.List<Map<String, String>>) res
                        .get("classifications");
                sse.sendEvent(jobId,
                        Map.of("stage", "[CLASS]", "msg",
                                "Entity Classification complete. Processed " + classifications.size() + " tables.",
                                "cls", "lok"));
                return classifications;
            }
            return null;
        } catch (Exception e) {
            sse.sendEvent(jobId, Map.of("stage", "[ERROR]", "msg", "Entity Classification failed: " + e.getMessage(),
                    "cls", "lwarn"));
            return null;
        }
    }
}
