package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Wire model for an extraction request. The monolith only handles
 * synchronous extractions; the async path was removed in v1.1.0.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonIgnoreProperties(ignoreUnknown = true)
public record ExtractionRequest(
        @JsonProperty("connection") ConnectionConfig connection,
        @JsonProperty("query") String query,
        @JsonProperty("output") OutputConfig output,
        @JsonProperty("options") ExtractionOptions options
) {

    @JsonCreator
    public ExtractionRequest {
        if (options == null) {
            options = ExtractionOptions.defaults();
        }
    }

    public ExtractionOptions optionsOrDefault() {
        return options == null ? ExtractionOptions.defaults() : options;
    }
}
