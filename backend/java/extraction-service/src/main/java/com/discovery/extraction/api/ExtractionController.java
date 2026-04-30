package com.discovery.extraction.api;

import com.discovery.extraction.core.ExtractionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST facade matching {@code openapi/extraction-service-v1.yaml} v1.1.0.
 * The monolith exposes one extraction endpoint and one connection-test
 * endpoint - no async / status / cancel surface.
 */
@RestController
@RequestMapping("/api/v1")
public class ExtractionController {

    private static final Logger log = LoggerFactory.getLogger(ExtractionController.class);

    private final ExtractionService service;

    public ExtractionController(ExtractionService service) {
        this.service = service;
    }

    @PostMapping("/extract")
    public ResponseEntity<ExtractionResponse> extract(@RequestBody ExtractionRequest request) {
        log.info("POST /api/v1/extract tag={}",
                request == null || request.options() == null ? null : request.options().tag());
        ExtractionResponse resp = service.extractSync(request);
        return ResponseEntity.ok(resp);
    }

    @PostMapping("/connections/test")
    public ResponseEntity<Void> testConnection(@RequestBody ConnectionConfig config) {
        service.testConnection(config);
        return ResponseEntity.ok().build();
    }

    /**
     * Live cardinality probe (Sprint 4).  Sync-only.  See
     * {@link com.discovery.extraction.core.ExtractionService#probeCardinality}.
     */
    @PostMapping("/probe-cardinality")
    public ResponseEntity<CardinalityProbeResponse> probeCardinality(
            @RequestBody CardinalityProbeRequest request) {
        log.info("POST /api/v1/probe-cardinality pairs={}",
                request == null || request.pairs() == null ? 0 : request.pairs().size());
        return ResponseEntity.ok(service.probeCardinality(request));
    }
}
