package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Tunables attached to a single extraction request.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonIgnoreProperties(ignoreUnknown = true)
public record ExtractionOptions(
        @JsonProperty("fetch_size") Integer fetchSize,
        @JsonProperty("timeout_seconds") Integer timeoutSeconds,
        @JsonProperty("max_rows") Long maxRows,
        @JsonProperty("tag") String tag
) {

    @JsonCreator
    public ExtractionOptions {
        if (fetchSize == null || fetchSize <= 0) {
            fetchSize = 10_000;
        }
        if (timeoutSeconds == null || timeoutSeconds <= 0) {
            // 7200s (2h) matches the Python client's request_timeout_seconds
            // ceiling per spec section 10. A 1h server-side default would
            // kill huge-table extractions while the client kept waiting.
            timeoutSeconds = 7_200;
        }
    }

    public static ExtractionOptions defaults() {
        return new ExtractionOptions(10_000, 7_200, null, null);
    }
}
