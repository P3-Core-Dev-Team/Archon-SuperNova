package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * One Parquet file produced by an extraction.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ManifestEntry(
        @JsonProperty("path") String path,
        @JsonProperty("rows") long rows,
        @JsonProperty("bytes") long bytes,
        @JsonProperty("checksum_sha256") String checksumSha256,
        @JsonProperty("row_groups") int rowGroups
) {
}
