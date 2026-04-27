package com.discovery.extraction.api;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Structured error payload for failed extractions or rejected requests.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ErrorInfo(
        @JsonProperty("code") String code,
        @JsonProperty("message") String message,
        @JsonProperty("retryable") boolean retryable
) {
    public static ErrorInfo of(String code, String message) {
        return new ErrorInfo(code, message, false);
    }

    public static ErrorInfo retryable(String code, String message) {
        return new ErrorInfo(code, message, true);
    }
}
