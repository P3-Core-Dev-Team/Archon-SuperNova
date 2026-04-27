package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Wire model for extraction output settings.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonIgnoreProperties(ignoreUnknown = true)
public record OutputConfig(
        @JsonProperty("path") String path,
        @JsonProperty("compression") String compression,
        @JsonProperty("compression_level") Integer compressionLevel,
        @JsonProperty("row_group_size") Integer rowGroupSize,
        @JsonProperty("page_size") Integer pageSize
) {

    @JsonCreator
    public OutputConfig {
        if (compression == null || compression.isBlank()) {
            compression = "zstd";
        }
        if (compressionLevel == null) {
            compressionLevel = 3;
        }
        if (rowGroupSize == null || rowGroupSize <= 0) {
            rowGroupSize = 100_000;
        }
        if (pageSize == null || pageSize <= 0) {
            pageSize = 1_048_576;
        }
    }
}
