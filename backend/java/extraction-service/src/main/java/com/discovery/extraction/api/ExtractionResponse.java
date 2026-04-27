package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.UUID;

/**
 * Wire model returned for synchronous extractions and connection-test results.
 * Aligned with {@code openapi/extraction-service-v1.yaml} v1.1.1.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractionResponse(
        @JsonProperty("extraction_id") UUID extractionId,
        @JsonProperty("status") ExtractionStatus status,
        @JsonProperty("manifest") Manifest manifest,
        @JsonProperty("error") ErrorInfo error
) {

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Manifest(
            @JsonProperty("files") List<ManifestEntry> files,
            @JsonProperty("duration_ms") long durationMs,
            @JsonProperty("rows_per_second") long rowsPerSecond,
            @JsonProperty("bytes_per_second") long bytesPerSecond
    ) {
    }

    public static ExtractionResponse success(UUID id, Manifest manifest) {
        return new ExtractionResponse(id, ExtractionStatus.COMPLETED, manifest, null);
    }

    public static ExtractionResponse failed(UUID id, ErrorInfo error) {
        return new ExtractionResponse(id, ExtractionStatus.FAILED, null, error);
    }

    public static ExtractionResponse running(UUID id) {
        return new ExtractionResponse(id, ExtractionStatus.RUNNING, null, null);
    }
}
