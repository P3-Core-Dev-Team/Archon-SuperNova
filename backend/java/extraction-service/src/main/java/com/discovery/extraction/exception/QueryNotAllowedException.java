package com.discovery.extraction.exception;

/**
 * Thrown by QueryWhitelistValidator when a caller-supplied SQL statement
 * is rejected by the server-side whitelist. Always maps to HTTP 400.
 */
public class QueryNotAllowedException extends ExtractionException {

    public QueryNotAllowedException(String reason) {
        super("QUERY_NOT_ALLOWED", reason, false, null);
    }

    public QueryNotAllowedException(String reason, Throwable cause) {
        super("QUERY_NOT_ALLOWED", reason, false, cause);
    }
}
